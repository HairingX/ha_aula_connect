"""The calendars of an Aula login: events, birthdays, weekly plans and newsletters."""

from calendar import isleap
from datetime import datetime, timedelta, date
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import now
from typing import Any, List
import logging

from .aula_calendar_coordinator import AulaCalendarCoordinator, AulaCalendarCoordinatorData
from .aula_data import AulaConfigEntry
from .aula_data_coordinator import AulaDataCoordinator
from aula_connect.api.models.constants import (
    AulaCalendarEventType, CALENDAR_EVENT_ICON, WEEKLY_PLAN_TASK_ICON,
    get_label,
)
from aula_connect.api.utils.list_utils import list_without_none
from aula_connect.api.models.module import (
    AulaBirthdayEvent,
    AulaChildProfile,
    AulaEasyiqDailyPlan,
    AulaEasyiqEvent,
    AulaEasyiqWeeklyPlan,
    AulaInstitutionProfile,
    AulaNewsletter,
    AulaProfile,
    AulaCalendarEvent,
    AulaCalendarEventTimeSlotEntryIndex,
    AulaDailyPlanTask,
    AulaWeeklyNewsletter,
    AulaWeeklyPlan,
    AulaDailyPlan,
)

from .entity import AulaCalendarEntityBase

_LOGGER = logging.getLogger(__name__)

def to_datetime(value: datetime | date, tzinfo: Any) -> datetime:
    """The datetime itself, or a date's midnight in `tzinfo`."""
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, datetime.min.time(), tzinfo)

def _is_event_in_range(event: CalendarEvent, start: datetime, end: datetime) -> bool:
    """Whether the event overlaps the range from `start` to `end`."""
    event_start = to_datetime(event.start, start.tzinfo)
    event_end = to_datetime(event.end, start.tzinfo)
    return event_start < end and event_end >= start

# The entities read what the coordinators fetched, so updating them never waits on Aula.
PARALLEL_UPDATES = 0


def celebrated_on(event: AulaBirthdayEvent, year: int) -> date:
    """The day the birthday falls on in `year`: 28 February for 29 February outside a leap year."""
    birthday = event.birthday_date
    if (birthday.month, birthday.day) == (2, 29) and not isleap(year):
        return date(year, 2, 28)
    return birthday.replace(year=year)


def is_meeting(event: AulaCalendarEvent) -> bool:
    """Whether the event is a meeting parents book a time slot in."""
    return event.type == AulaCalendarEventType.SCHOOL_HOME_MEETING or event.type == AulaCalendarEventType.PARENTAL_MEETING


def required_children(profile: AulaChildProfile | AulaProfile, institution: AulaInstitutionProfile, event: AulaCalendarEvent) -> List[AulaChildProfile]:
    """The children of the profile the event belongs to; for a child, the child itself if it does."""
    result = list[AulaChildProfile]()
    if isinstance(profile, AulaChildProfile):
        if institution.id in event.belongs_to_profiles:
            result.append(profile)
    else:
        for child in profile.children:
            if child.id in event.belongs_to_profiles:
                result.append(child)
    return result


def shown_for(profile: AulaChildProfile | AulaProfile, institution: AulaInstitutionProfile, event: AulaCalendarEvent) -> bool:
    """Whether the profile's events at the institution show the event."""
    if isinstance(profile, AulaChildProfile) and is_meeting(event):
        # These meetings may be for the parents alone, which a child's calendar leaves out.
        return len(required_children(profile, institution, event)) > 0
    return True


def booked_time_slot(institution: AulaInstitutionProfile, event: AulaCalendarEvent) -> AulaCalendarEventTimeSlotEntryIndex | None:
    """The time booked for the institution profile in a meeting; None when none is."""
    if event.time_slot is None:
        return None
    for slot in event.time_slot.time_slots:
        for answer in slot.answers:
            if answer.inst_profile_id == institution.id or answer.concerning_profile_id == institution.id:
                if len(slot.time_slot_indexes) > answer.selected_time_slot_index:
                    return slot.time_slot_indexes[answer.selected_time_slot_index]
    return None


async def async_setup_entry(hass: HomeAssistant, entry: AulaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add the calendars of the entry's profiles and children, each kind where Aula offers it."""
    data_coordinator = entry.runtime_data.data_coordinator
    calendar_coordinator = entry.runtime_data.calendar_coordinator
    entities: List[CalendarEntity] = []
    for child in data_coordinator.data.children:
        entities.append(AulaEventCalendar(calendar_coordinator, data_coordinator, child, child.institution_profile))
    for profile in data_coordinator.data.profiles:
        for institution in profile.institution_profiles:
            entities.append(AulaEventCalendar(calendar_coordinator, data_coordinator, profile, institution))

    if data_coordinator.weekly_plans_supported():
        for child in data_coordinator.data.children:
            entities.append(AulaWeeklyPlanCalendar(calendar_coordinator, data_coordinator, child, child.institution_profile))
            entities.append(AulaBirthdayCalendar(calendar_coordinator, data_coordinator, child, child.institution_profile))

    if data_coordinator.easyiq_weekplan_supported():
        for child in data_coordinator.data.children:
            entities.append(AulaEasyiqWeekplanCalendar(calendar_coordinator, data_coordinator, child, child.institution_profile))

    if data_coordinator.newsletter_supported():
        for child in data_coordinator.data.children:
            entities.append(AulaNewsletterCalendar(calendar_coordinator, data_coordinator, child, child.institution_profile))

    async_add_entities(entities)

class AulaBirthdayCalendar(AulaCalendarEntityBase, CalendarEntity):
    """The birthdays at a child's institution."""
    _event: CalendarEvent | None = None
    _profile: AulaChildProfile
    _institution: AulaInstitutionProfile
    _data_coordinator: AulaDataCoordinator

    def __init__(self, calendar_coordinator: AulaCalendarCoordinator, data_coordinator: AulaDataCoordinator, profile: AulaChildProfile, institution: AulaInstitutionProfile):
        super().__init__(calendar_coordinator, name="birthdays")
        self._data_coordinator = data_coordinator
        self._profile = profile
        first_name = profile.first_name
        institution_name = institution.institution_name
        self._attr_unique_id = f"{self._attr_unique_id}_{first_name}_{institution_name}"
        self._attr_translation_placeholders = { "name": first_name, "institution": institution_name }

    async def async_will_remove_from_hass(self):
        """Stop having the coordinator read the child's birthdays."""
        await self.coordinator.async_remove_birthday_key(self._profile)
        return await super().async_will_remove_from_hass()

    async def async_added_to_hass(self):
        """Have the coordinator read the child's birthdays."""
        await self.coordinator.async_add_birthday_key(self._profile)
        return await super().async_added_to_hass()

    def _handle_data_updated(self, data: AulaCalendarCoordinatorData) -> bool:
        if self._profile.id in data.updated_birthdays_for_listener_keys:
            return True
        return False

    @property
    def event(self) -> CalendarEvent | None:
        """The next birthday, today's included."""
        return self._get_active_or_upcoming_event()

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> List[CalendarEvent]:
        """The birthdays in the range, each in every year of the range it falls in."""
        birthdays = await self.coordinator.get_birthdays_for_interval([self._profile], start_date, end_date)
        events = list[CalendarEvent]()
        for birthday in birthdays:
            for year in range(start_date.year, end_date.year + 1):
                event = self._create_calendar_event(birthday, year)
                if _is_event_in_range(event, start_date, end_date):
                    events.append(event)
        return events

    def _get_active_or_upcoming_event(self) -> CalendarEvent | None:
        today = now().date()
        upcoming = [
            (start, birthday)
            for birthday in self.coordinator.get_birthdays(self._profile)
            for start in (celebrated_on(birthday, today.year), celebrated_on(birthday, today.year + 1))
            if start >= today
        ]
        if not upcoming:
            return None
        start, birthday = min(upcoming, key=lambda item: item[0])
        return self._create_calendar_event(birthday, start.year)

    def _create_calendar_event(self, event: AulaBirthdayEvent, year: int) -> CalendarEvent:
        icon = CALENDAR_EVENT_ICON.get(AulaCalendarEventType.BIRTHDAY, "")
        age = year - event.birthday_date.year
        summary = f"{icon} {event.full_name} ({event.main_group_name}) {get_label('birthday_turns')} {age}"
        start_date = celebrated_on(event, year)
        return CalendarEvent(
            uid=str(event.institution_profile_id),
            summary=summary,
            start=start_date,
            end=start_date + timedelta(days=1),
        )


class AulaEventCalendar(AulaCalendarEntityBase, CalendarEntity):
    """The events of a profile or a child at one institution."""
    _event: CalendarEvent | None = None
    _profile: AulaChildProfile|AulaProfile
    _institution: AulaInstitutionProfile
    _data_coordinator: AulaDataCoordinator

    def __init__(self, calendar_coordinator: AulaCalendarCoordinator, data_coordinator: AulaDataCoordinator, profile: AulaChildProfile|AulaProfile, institution: AulaInstitutionProfile):
        super().__init__(calendar_coordinator, name="events")
        self._data_coordinator = data_coordinator
        self._profile = profile
        self._institution = institution
        first_name = profile.first_name
        institution_name = institution.institution_name
        self._attr_unique_id = f"{self._attr_unique_id}_{first_name}_{institution_name}"
        self._attr_translation_placeholders = { "name": first_name, "institution": institution_name }

    async def async_will_remove_from_hass(self):
        """Stop having the coordinator read the institution's events."""
        await self.coordinator.async_remove_event_key(self._institution)
        return await super().async_will_remove_from_hass()

    async def async_added_to_hass(self):
        """Have the coordinator read the institution's events."""
        await self.coordinator.async_add_event_key(self._institution)
        return await super().async_added_to_hass()

    def _handle_data_updated(self, data: AulaCalendarCoordinatorData) -> bool:
        if self._institution.id in data.updated_events_for_listener_keys:
            return True
        return False

    @property
    def event(self) -> CalendarEvent | None:
        """The event going on now, or else the next one."""
        return self._get_active_or_upcoming_event()

    def _event_filter(self, event: AulaCalendarEvent) -> bool:
        """Whether the event is shown in this calendar."""
        return shown_for(self._profile, self._institution, event)

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> List[CalendarEvent]:
        """The events in the range, read from Aula now."""
        result_items = await self.coordinator.get_events_for_interval([self._institution], start_date, end_date)
        events = list_without_none(self._create_calendar_event(event) for event in filter(self._event_filter, result_items))
        return [e for e in events if _is_event_in_range(e, start_date, end_date)]

    def _get_active_or_upcoming_event(self) -> CalendarEvent | None:
        current_time = now()
        events = self.coordinator.get_events(self._institution)
        for event in filter(self._event_filter,events):
            if event.end_datetime >= current_time:
                calendar_event = self._create_calendar_event(event)
                if calendar_event is not None and calendar_event.end >= current_time:
                    return calendar_event
        return None

    def _create_calendar_event(self, event: AulaCalendarEvent) -> CalendarEvent|None:
        """The calendar event that shows Aula's event, at the booked time slot of a meeting."""
        icon = CALENDAR_EVENT_ICON.get(event.type, "")
        summary = event.title
        details = list[str]()
        start_datetime = event.start_datetime
        end_datetime = event.end_datetime
        location:str|None = None
        if event.primary_resource is not None:
            location = event.primary_resource.name

        # A meeting spans every day parents can book a time slot on; once booked, only the slot is shown.
        if is_meeting(event) and event.time_slot is not None:
            required = required_children(self._profile, self._institution, event)
            booked = booked_time_slot(self._institution, event)
            if booked is None:
                summary += " <!>"
            else:
                start_datetime = booked.start_datetime
                end_datetime = booked.end_datetime
                summary = f"{summary} {self._institution.institution_name}"
                if location is None: location = self._institution.institution_name
                if required: details.append(f"{get_label('bring_children')} {", ".join(child.name for child in required)}")

        if event.lesson is not None and event.lesson.participants:
            initials = ", ".join(
                p.teacher_initials for p in event.lesson.participants if p.teacher_initials
            )
            if initials:
                summary = f"{summary} ({initials})"
            if event.lesson.lesson_status == "substitute":
                summary = f"{summary} - {get_label('substitute')}"
            teachers = ", ".join(
                p.teacher_name for p in event.lesson.participants if p.teacher_name
            )
            if teachers:
                details.append(teachers)

        if event.response_status and event.response_status != "accepted":
            label = get_label(f"response_{event.response_status}")
            details.append(f"{get_label('status_prefix')}: {label}")

        summary = f"{icon} {summary}" if icon else summary
        description = "\n".join(details)

        return CalendarEvent(
            uid=str(event.id),
            summary=summary,
            start=start_datetime,
            end=end_datetime,
            description=None if len(description.strip()) == 0 else description,
            location=location,
        )


class AulaWeeklyPlanCalendar(AulaCalendarEntityBase, CalendarEntity):
    """The tasks of a child's weekly plans, each on its day."""
    _event: CalendarEvent | None = None
    _profile: AulaChildProfile
    _institution: AulaInstitutionProfile
    _data_coordinator: AulaDataCoordinator

    def __init__(self, calendar_coordinator: AulaCalendarCoordinator, data_coordinator: AulaDataCoordinator, profile: AulaChildProfile, institution: AulaInstitutionProfile):
        super().__init__(calendar_coordinator, name="weekly_plan")
        self._data_coordinator = data_coordinator
        self._profile = profile
        first_name = profile.first_name
        institution_name = institution.institution_name
        self._attr_unique_id = f"{self._attr_unique_id}_{first_name}_{institution_name}"
        self._attr_translation_placeholders = { "name": first_name, "institution": institution_name }

    async def async_will_remove_from_hass(self):
        """Stop having the coordinator read the child's weekly plans."""
        await self.coordinator.async_remove_weekly_plan_key(self._profile)
        return await super().async_will_remove_from_hass()

    async def async_added_to_hass(self):
        """Have the coordinator read the child's weekly plans."""
        await self.coordinator.async_add_weekly_plan_key(self._profile)
        return await super().async_added_to_hass()

    def _handle_data_updated(self, data: AulaCalendarCoordinatorData) -> bool:
        if self._profile.id in data.updated_weekly_plans_for_listener_keys:
            return True
        return False

    @property
    def event(self) -> CalendarEvent | None:
        """The first task of today, or else of the next day that has one."""
        return self._get_active_or_upcoming_event()

    def _filter_plan(self, dailyplan: AulaDailyPlan) -> bool:
        """Return True if the daily plan is visible."""
        return True

    def _filter_task(self, task: AulaDailyPlanTask) -> bool:
        """Return True if the task is visible."""
        return True

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> List[CalendarEvent]:
        """The tasks in the range, read from Aula now."""
        weekplans = await self.coordinator.get_weekly_plans_for_interval([self._profile], start_date, end_date)
        events = list[CalendarEvent]()
        for weekplan, dayplan, task in self._weekplans_iterable(weekplans):
            event = self._create_calendar_event(weekplan, dayplan, task)
            if event and _is_event_in_range(event, start_date, end_date):
                events.append(event)
        return events

    def _get_active_or_upcoming_event(self) -> CalendarEvent | None:
        current_time = now()
        END_DELTA = timedelta(days=1)
        weekplans = self.coordinator.get_weekly_plans(self._profile)
        for weekplan, dayplan, task in self._weekplans_iterable(weekplans):
            if dayplan.date +  END_DELTA > current_time.date():
                calendar_event = self._create_calendar_event(weekplan, dayplan, task)
                if calendar_event.end > current_time.date():
                    return calendar_event
        return None

    def _weekplans_iterable(self, weekplans: List[AulaWeeklyPlan]):
        for weekplan in weekplans:
            for dailyplan in weekplan.daily_plans:
                if not self._filter_plan(dailyplan): continue
                for task in dailyplan.tasks:
                    if not self._filter_task(task): continue
                    yield weekplan, dailyplan, task

    def _create_calendar_event(self, weekplan: AulaWeeklyPlan, dailyplan: AulaDailyPlan, task: AulaDailyPlanTask) -> CalendarEvent:
        """The all-day calendar event that shows a task of the plan."""
        icon = WEEKLY_PLAN_TASK_ICON.get(task.type, "")
        summary = task.title if task.title else task.content
        if len(summary) > 75: summary = summary[:73] + "..."
        summaryparts = summary.split("\n", 1)
        summary = summaryparts[0]
        if len(summaryparts) > 1: summary += " ..."
        if task.pill:
            summary = f"[{task.pill}] {summary}"
        summary = f"{icon} {summary}" if icon else summary

        description_parts: list[str] = []
        if task.title:
            description_parts.append(task.title)
        description_parts.append(task.content)
        if task.group:
            description_parts.append(task.group)
        if task.author:
            description_parts.append(task.author)
        description = "\n".join(description_parts)

        start_date = dailyplan.date
        end_date = start_date + timedelta(days=1)

        return CalendarEvent(
            uid=str(task.id),
            summary=summary,
            start=start_date,
            end=end_date,
            description=None if len(description.strip()) == 0 else description,
        )


class AulaEasyiqWeekplanCalendar(AulaCalendarEntityBase, CalendarEntity):
    """The events of a child's EasyIQ weekly plans."""
    _profile: AulaChildProfile
    _institution: AulaInstitutionProfile
    _data_coordinator: AulaDataCoordinator

    def __init__(self, calendar_coordinator: AulaCalendarCoordinator, data_coordinator: AulaDataCoordinator, profile: AulaChildProfile, institution: AulaInstitutionProfile):
        super().__init__(calendar_coordinator, name="easyiq_weekplan")
        self._data_coordinator = data_coordinator
        self._profile = profile
        first_name = profile.first_name
        institution_name = institution.institution_name
        self._attr_unique_id = f"{self._attr_unique_id}_{first_name}_{institution_name}"
        self._attr_translation_placeholders = { "name": first_name, "institution": institution_name }

    async def async_will_remove_from_hass(self):
        """Stop having the coordinator read the child's EasyIQ weekly plans."""
        await self.coordinator.async_remove_easyiq_weekplan_key(self._profile)
        return await super().async_will_remove_from_hass()

    async def async_added_to_hass(self):
        """Have the coordinator read the child's EasyIQ weekly plans."""
        await self.coordinator.async_add_easyiq_weekplan_key(self._profile)
        return await super().async_added_to_hass()

    def _handle_data_updated(self, data: AulaCalendarCoordinatorData) -> bool:
        if self._profile.id in data.updated_easyiq_weekplans_for_listener_keys:
            return True
        return False

    @property
    def event(self) -> CalendarEvent | None:
        """The first event of the plans that has not ended."""
        current_time = now()
        plans = self.coordinator.get_easyiq_weekly_plans(self._profile)
        for _plan, dayplan, event in self._plans_iterable(plans):
            event_end = event.end
            if event_end >= current_time:
                return self._create_calendar_event(dayplan, event)
        return None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> List[CalendarEvent]:
        """The events in the range, read from Aula now."""
        plans = await self.coordinator.get_easyiq_weekly_plans_for_interval([self._profile], start_date, end_date)
        events = list[CalendarEvent]()
        for _plan, dayplan, event in self._plans_iterable(plans):
            calendar_event = self._create_calendar_event(dayplan, event)
            if _is_event_in_range(calendar_event, start_date, end_date):
                events.append(calendar_event)
        return events

    def _plans_iterable(self, plans: List[AulaEasyiqWeeklyPlan]):
        for plan in plans:
            for dayplan in plan.daily_plans:
                for event in dayplan.events:
                    yield plan, dayplan, event

    def _create_calendar_event(self, dayplan: AulaEasyiqDailyPlan, event: AulaEasyiqEvent) -> CalendarEvent:
        summary = event.title if event.title else event.description
        if len(summary) > 75: summary = summary[:73] + "..."
        summaryparts = summary.split("\n", 1)
        summary = summaryparts[0]
        if len(summaryparts) > 1: summary += " ..."

        description = event.description
        if event.owner_name:
            description = f"{event.owner_name}\n{description}" if description else event.owner_name

        start = event.start
        end = event.end

        return CalendarEvent(
            uid=str(event.id),
            summary=summary,
            start=start,
            end=end,
            description=None if not description or len(description.strip()) == 0 else description,
        )


class AulaNewsletterCalendar(AulaCalendarEntityBase, CalendarEntity):
    """A child's MinUddannelse newsletters, each over the days of its week with its HTML as description."""
    _profile: AulaChildProfile
    _institution: AulaInstitutionProfile
    _data_coordinator: AulaDataCoordinator

    def __init__(self, calendar_coordinator: AulaCalendarCoordinator, data_coordinator: AulaDataCoordinator, profile: AulaChildProfile, institution: AulaInstitutionProfile):
        super().__init__(calendar_coordinator, name="newsletter")
        self._data_coordinator = data_coordinator
        self._profile = profile
        first_name = profile.first_name
        institution_name = institution.institution_name
        self._attr_unique_id = f"{self._attr_unique_id}_{first_name}_{institution_name}"
        self._attr_translation_placeholders = { "name": first_name, "institution": institution_name }

    async def async_will_remove_from_hass(self):
        """Stop having the coordinator read the child's newsletters."""
        await self.coordinator.async_remove_newsletter_key(self._profile)
        return await super().async_will_remove_from_hass()

    async def async_added_to_hass(self):
        """Have the coordinator read the child's newsletters."""
        await self.coordinator.async_add_newsletter_key(self._profile)
        return await super().async_added_to_hass()

    @property
    def event(self) -> CalendarEvent | None:
        """The first newsletter of the first week that has not ended."""
        current_time = now()
        newsletters = self.coordinator.get_newsletters(self._profile)
        for weekly in newsletters:
            if weekly.to_date >= current_time.date():
                for newsletter in weekly.newsletters:
                    return self._create_calendar_event(weekly, newsletter)
        return None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> List[CalendarEvent]:
        """The newsletters in the range, read from Aula now."""
        newsletters = await self.coordinator.get_newsletters_for_interval(
[self._profile], start_date, end_date)
        events = list[CalendarEvent]()
        for weekly in newsletters:
            for newsletter in weekly.newsletters:
                event = self._create_calendar_event(weekly, newsletter)
                if _is_event_in_range(event, start_date, end_date):
                    events.append(event)
        return events

    def _handle_data_updated(self, data: AulaCalendarCoordinatorData) -> bool:
        if self._profile.id in data.updated_newsletters_for_listener_keys:
            return True
        return False

    def _create_calendar_event(self, weekly: AulaWeeklyNewsletter, newsletter: AulaNewsletter) -> CalendarEvent:
        summary = f"Newsletter - {weekly.child_name}"
        if newsletter.institution_name:
            summary = f"{summary} ({newsletter.institution_name})"

        return CalendarEvent(
            uid=str(newsletter.id),
            summary=summary,
            start=weekly.from_date,
            end=weekly.to_date + timedelta(days=1),
            description=newsletter.content_html,
        )
