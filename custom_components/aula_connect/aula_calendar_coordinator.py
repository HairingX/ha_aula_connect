"""The coordinator that reads the calendars of an Aula login: birthdays, events, plans and newsletters."""

from dataclasses import dataclass
from datetime import timedelta
import datetime
from collections.abc import Awaitable
from typing import Dict, List, TypeVar
from homeassistant.util.dt import now
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .redact import error_text
from .const import DOMAIN
import asyncio
import logging

from aula_connect.api.models.module import (
    AulaInstitutionProfile,
    AulaBirthdayEvent,
    AulaCalendarEvent,
    AulaChildProfile,
    AulaEasyiqWeeklyPlan,
    AulaWeeklyNewsletter,
    AulaWeeklyPlan,
)
from aula_connect.client import AulaClient
from aula_connect.api.aula_errors import AulaCredentialError

_LOGGER = logging.getLogger(__name__)

# Polled often, so that each calendar is read again soon after midnight; each kind is read from
# Aula only at its own interval.
COORDINATOR_POLL_INTERVAL = timedelta(minutes=10)

BIRTHDAYS_UPDATE_INTERVAL = timedelta(days=1)
EVENTS_UPDATE_INTERVAL = timedelta(hours=2)
WEEKLY_PLANS_UPDATE_INTERVAL = timedelta(hours=2)
EASYIQ_WEEKPLAN_UPDATE_INTERVAL = timedelta(hours=2)
NEWSLETTER_UPDATE_INTERVAL = timedelta(hours=2)

SYNC_EVENT_MAX_TIME = timedelta(weeks=2)
"""How far ahead the calendars are read."""

MAX_CONSECUTIVE_FAILURES = 3
"""How many reads in a row may fail in part before the coordinator reports a failure."""

@dataclass
class AulaCalendarCoordinatorData:
    """The ids of the profiles whose calendars a refresh read anew, by kind of calendar."""

    updated_birthdays_for_listener_keys: List[int]
    updated_events_for_listener_keys: List[int]
    updated_weekly_plans_for_listener_keys: List[int]
    updated_easyiq_weekplans_for_listener_keys: List[int]
    updated_newsletters_for_listener_keys: List[int]

T = TypeVar("T", AulaInstitutionProfile, AulaChildProfile)
class AulaCalendarCoordinatorMeta[T]:
    """The calendars that follow one profile's data, and when it was last read."""

    keys: List[T]
    last_updated: datetime.datetime|None = None
    def __init__(self):
        self.keys = []

class AulaCalendarCoordinator(DataUpdateCoordinator[AulaCalendarCoordinatorData]):
    """Reads the calendars that entities follow, each kind at its own interval, and keeps them."""

    _birthdaymap: dict[int, List[AulaBirthdayEvent]]
    _weeklyplanmap: dict[int, List[AulaWeeklyPlan]]
    _easyiqweekplanmap: dict[int, List[AulaEasyiqWeeklyPlan]]
    _newslettermap: dict[int, List[AulaWeeklyNewsletter]]
    _eventmap: dict[int, List[AulaCalendarEvent]]
    _client: AulaClient
    _birthday_listeners: dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]
    _event_listeners: dict[int, AulaCalendarCoordinatorMeta[AulaInstitutionProfile]]
    _weekly_plan_listeners: dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]
    _easyiq_weekplan_listeners: dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]
    _newsletter_listeners: dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]

    def __init__(self, device_id: str, hass: HomeAssistant, client: AulaClient, config_entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            name="calendar",
            config_entry=config_entry,
            update_interval=COORDINATOR_POLL_INTERVAL,
            always_update=True,
        )
        self._birthdaymap = dict()
        self._eventmap = dict()
        self._weeklyplanmap = dict()
        self._easyiqweekplanmap = dict()
        self._newslettermap = dict()
        self._birthday_listeners = dict()
        self._event_listeners = dict()
        self._weekly_plan_listeners = dict()
        self._easyiq_weekplan_listeners = dict()
        self._newsletter_listeners = dict()
        self._consecutive_failures = 0
        self._partly_unreadable = False
        self._client = client
        self.device_id = device_id
        self.aula_version = client.aula_version

        _LOGGER.debug(f"Coordinator Calendar {device_id} initialized")

    async def _async_setup(self) -> None:
        """Check that Aula can be reached with the entry's login."""
        try:
            await self._client.connection_check()
        except AulaCredentialError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="login_expired") from err
        except Exception as err:
            # Not chained: the error's own text holds the access token, which a traceback would log.
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cannot_connect", translation_placeholders={"error": error_text(err)}
            ) from None

    async def _async_update_data(self) -> AulaCalendarCoordinatorData:
        """Read the followed calendars that are due, keeping the others as they were read."""
        try:
            async with asyncio.timeout(120):
                data = await self._fetch_data(self._birthday_listeners, self._event_listeners, self._weekly_plan_listeners, self._easyiq_weekplan_listeners, self._newsletter_listeners)
                if isinstance(data, Exception):
                    raise data
                return data
        except AulaCredentialError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="login_expired") from err
        except Exception as err:
            # Not chained: the error's own text holds the access token, which a traceback would log.
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cannot_connect", translation_placeholders={"error": error_text(err)}
            ) from None

    async def _fetch_data(self, birthdaylisteners: Dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]], eventlisteners: Dict[int, AulaCalendarCoordinatorMeta[AulaInstitutionProfile]], weekplanlisteners: Dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]], easyiqweekplanlisteners: Dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]], newsletterlisteners: Dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]) -> AulaCalendarCoordinatorData|Exception:
        try:
            logindata = await self._client.login()
            self.aula_version = logindata.api_version
        except AulaCredentialError:
            raise
        except Exception as ex:
            return ex

        # Each kind is read on its own, so that one failing keeps none of the others from updating.
        had_failure = False
        failures = list[str]()

        new_birtyday_keys = list[int]()
        try:
            new_birthdaymap, new_birtyday_keys = await self._fetch_birthdays(birthdaylisteners)
            self._birthdaymap = new_birthdaymap
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"birthdays: {error_text(ex)}")

        new_event_keys = list[int]()
        try:
            new_eventmap, new_event_keys = await self._fetch_events(eventlisteners)
            self._eventmap = new_eventmap
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"events: {error_text(ex)}")

        new_weeklyplan_keys = list[int]()
        try:
            new_weeklyplanmap, new_weeklyplan_keys = await self._fetch_weekly_plans(weekplanlisteners)
            self._weeklyplanmap = new_weeklyplanmap
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"weekly plans: {error_text(ex)}")

        new_easyiq_keys = list[int]()
        try:
            new_easyiqweekplanmap, new_easyiq_keys = await self._fetch_easyiq_weekly_plans(easyiqweekplanlisteners)
            self._easyiqweekplanmap = new_easyiqweekplanmap
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"EasyIQ weekplans: {error_text(ex)}")

        new_newsletter_keys = list[int]()
        try:
            new_newslettermap, new_newsletter_keys = await self._fetch_newsletters(newsletterlisteners)
            self._newslettermap = new_newslettermap
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"newsletters: {error_text(ex)}")

        if had_failure:
            # Logged once when Aula's calendars can be read only in part, and once when in full.
            if not self._partly_unreadable:
                self._partly_unreadable = True
                _LOGGER.warning("Aula's calendars could be read only in part, using cached data: %s", "; ".join(failures))
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self._consecutive_failures = 0
                return Exception(f"Aula API failed {MAX_CONSECUTIVE_FAILURES} consecutive times")
        else:
            if self._partly_unreadable:
                self._partly_unreadable = False
                _LOGGER.info("Aula's calendars can be read in full again")
            self._consecutive_failures = 0

        for key in new_birtyday_keys:
            meta = birthdaylisteners.get(key)
            if meta: meta.last_updated = now()

        for key in new_event_keys:
            meta = eventlisteners.get(key)
            if meta: meta.last_updated = now()

        for key in new_weeklyplan_keys:
            meta = weekplanlisteners.get(key)
            if meta: meta.last_updated = now()

        for key in new_easyiq_keys:
            meta = easyiqweekplanlisteners.get(key)
            if meta: meta.last_updated = now()

        for key in new_newsletter_keys:
            meta = newsletterlisteners.get(key)
            if meta: meta.last_updated = now()

        return AulaCalendarCoordinatorData(
            updated_birthdays_for_listener_keys=new_birtyday_keys,
            updated_events_for_listener_keys=new_event_keys,
            updated_weekly_plans_for_listener_keys=new_weeklyplan_keys,
            updated_easyiq_weekplans_for_listener_keys=new_easyiq_keys,
            updated_newsletters_for_listener_keys=new_newsletter_keys,
        )


    async def _read_period[R](self, what: str, read: Awaitable[List[R]]) -> List[R]:
        """What `read` reads from Aula; nothing, and a warning, when Aula cannot be read.

        Raises:
            ConfigEntryAuthFailed: Aula no longer accepts the login; logging in again is asked for.
        """
        try:
            return await read
        except AulaCredentialError as err:
            # Home Assistant asks to log in again by itself only for an error raised by a refresh.
            entry = self.config_entry
            assert entry is not None
            entry.async_start_reauth(self.hass)
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="login_expired") from err
        except Exception as ex:
            _LOGGER.warning("Aula could not read the %s for the period: %s", what, error_text(ex))
            return []

    @staticmethod
    def _day_read(meta: AulaCalendarCoordinatorMeta[AulaInstitutionProfile] | AulaCalendarCoordinatorMeta[AulaChildProfile] | None) -> datetime.date | None:
        """The day the profile's data was last read on; None when it was not."""
        if meta is None or meta.last_updated is None:
            return None
        return meta.last_updated.date()

    def birthdays_read_for(self, profile: AulaChildProfile) -> tuple[datetime.date, datetime.date] | None:
        """The first day and the day after the last that the child's birthdays were last read for; None when they were not."""
        day = self._day_read(self._birthday_listeners.get(profile.id))
        if day is None:
            return None
        # Aula answers up to the end of the last day asked for.
        return (day, day + SYNC_EVENT_MAX_TIME + timedelta(days=1))

    def events_read_for(self, profile: AulaInstitutionProfile) -> tuple[datetime.date, datetime.date] | None:
        """The first day and the day after the last that the profile's events were last read for; None when they were not."""
        day = self._day_read(self._event_listeners.get(profile.id))
        if day is None:
            return None
        return (day, day + SYNC_EVENT_MAX_TIME)

    def weekly_plans_read_for(self, profile: AulaChildProfile) -> tuple[datetime.date, datetime.date] | None:
        """The first day and the day after the last that the child's weekly plans were last read for; None when they were not."""
        day = self._day_read(self._weekly_plan_listeners.get(profile.id))
        if day is None:
            return None
        # Meebook answers whole weeks, from the Monday of the first day asked for.
        return (day - timedelta(days=day.weekday()), day + SYNC_EVENT_MAX_TIME)

    async def get_birthdays_for_interval(self, profiles: List[AulaChildProfile], start_datetime: datetime.datetime, end_datetime: datetime.datetime) -> List[AulaBirthdayEvent]:
        """Aula's birthdays of the period, read now; none when Aula cannot be read."""
        return await self._read_period("birthdays", self._client.get_birthday_events(profiles, start_datetime, end_datetime))

    def get_birthdays(self, profile: AulaChildProfile) -> List[AulaBirthdayEvent]:
        """The birthdays last read for the child."""
        return self._birthdaymap.get(profile.id, [])

    async def async_add_birthday_key(self, profile: AulaChildProfile) -> None:
        """Follow the child's birthdays, and read what is due now."""
        id = profile.id
        meta = self._birthday_listeners.setdefault(id, AulaCalendarCoordinatorMeta[AulaChildProfile]())
        _LOGGER.debug(f"async_add_birthday_key {id}")
        meta.keys.append(profile)
        self._birthdaymap.setdefault(id, [])
        await self.async_refresh()

    async def async_remove_birthday_key(self, profile: AulaChildProfile) -> None:
        """Stop following the child's birthdays for one calendar; forget them when none follows them."""
        id = profile.id
        meta = self._birthday_listeners.get(id)
        if meta is None: return
        meta.keys.remove(profile)
        _LOGGER.debug(f"async_remove_birthday_key {id}")
        if len(meta.keys) == 0:
            self._birthday_listeners.pop(id)
            self._birthdaymap.pop(id)

    async def _fetch_birthdays(self, listeners: Dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]):
        request_data_profiles = list[AulaChildProfile]()
        new_birthdaymap = dict[int, List[AulaBirthdayEvent]]()
        for (id, meta) in listeners.items():
            if meta.last_updated is None or meta.last_updated < now() - BIRTHDAYS_UPDATE_INTERVAL or meta.last_updated.date() < now().date():
                request_data_profiles.append(meta.keys[0])
            else:
                new_birthdaymap[id] = self._birthdaymap.get(id, list[AulaBirthdayEvent]())

        for profile in request_data_profiles:
            # Aula's answer for several profiles cannot be split between them, so each is read alone.
            birthdays = await self._client.get_birthday_events([profile], now(), now() + SYNC_EVENT_MAX_TIME)
            _LOGGER.debug(f"Fetching birthdays for id: {profile.id}, got {len(birthdays)} birthdays")
            new_birthdaymap[profile.id] = birthdays

        return (new_birthdaymap, [inst.id for inst in request_data_profiles])

    async def get_events_for_interval(self, profiles: List[AulaInstitutionProfile], start_date: datetime.datetime, end_date: datetime.datetime) -> List[AulaCalendarEvent]:
        """Aula's events of the period, read now; none when Aula cannot be read."""
        return await self._read_period("events", self._client.get_calendar_events(profiles, start_date, end_date))

    def get_events(self, profile: AulaInstitutionProfile) -> List[AulaCalendarEvent]:
        """The events last read for the institution profile."""
        return self._eventmap.get(profile.id, [])

    async def async_add_event_key(self, profile: AulaInstitutionProfile) -> None:
        """Follow the institution profile's events, and read what is due now."""
        id = profile.id
        meta = self._event_listeners.setdefault(id, AulaCalendarCoordinatorMeta[AulaInstitutionProfile]())
        _LOGGER.debug(f"async_add_event_key {id}")
        meta.keys.append(profile)
        self._eventmap.setdefault(id, [])
        await self.async_refresh()

    async def async_remove_event_key(self, profile: AulaInstitutionProfile) -> None:
        """Stop following the profile's events for one calendar; forget them when none follows them."""
        id = profile.id
        meta = self._event_listeners.get(id)
        if meta is None: return
        meta.keys.remove(profile)
        _LOGGER.debug(f"async_remove_event_key {id}")
        if len(meta.keys) == 0:
            self._event_listeners.pop(id)
            self._eventmap.pop(id)

    async def _fetch_events(self, listeners: Dict[int, AulaCalendarCoordinatorMeta[AulaInstitutionProfile]]):
        request_data_institutions = list[AulaInstitutionProfile]()
        new_eventmap = dict[int, List[AulaCalendarEvent]]()
        for (id, meta) in listeners.items():
            if meta.last_updated is None or meta.last_updated < now() - EVENTS_UPDATE_INTERVAL or meta.last_updated.date() < now().date():
                request_data_institutions.append(meta.keys[0])
            else:
                new_eventmap[id] = self._eventmap.get(id, list[AulaCalendarEvent]())
        for institution in request_data_institutions:
            # Aula's answer for several profiles cannot be split between them, so each is read alone.
            events = await self._client.get_calendar_events([institution], now(), now() + SYNC_EVENT_MAX_TIME)
            _LOGGER.debug(f"Fetching events for id: {institution.id}, got {len(events)} events")
            new_eventmap[institution.id] = events
        return (new_eventmap, [inst.id for inst in request_data_institutions])

    async def get_weekly_plans_for_interval(self, profiles: List[AulaChildProfile], start_datetime: datetime.datetime, end_datetime: datetime.datetime) -> List[AulaWeeklyPlan]:
        """Aula's weekly plans of the period, read now; none when Aula cannot be read."""
        return await self._read_period("weekly plans", self._client.get_weekly_plans(profiles, start_datetime, end_datetime))

    def get_weekly_plans(self, profile: AulaChildProfile) -> List[AulaWeeklyPlan]:
        """The weekly plans last read for the child."""
        return self._weeklyplanmap.get(profile.id, [])

    async def async_add_weekly_plan_key(self, profile: AulaChildProfile) -> None:
        """Follow the child's weekly plans, and read what is due now."""
        id = profile.id
        meta = self._weekly_plan_listeners.setdefault(id, AulaCalendarCoordinatorMeta[AulaChildProfile]())
        _LOGGER.debug(f"async_add_weekly_plan_key {id}")
        meta.keys.append(profile)
        self._weeklyplanmap.setdefault(id, [])
        await self.async_refresh()

    async def async_remove_weekly_plan_key(self, profile: AulaChildProfile) -> None:
        """Stop following the child's weekly plans for one calendar; forget them when none follows them."""
        id = profile.id
        meta = self._weekly_plan_listeners.get(id)
        if meta is None: return
        meta.keys.remove(profile)
        _LOGGER.debug(f"async_remove_weekly_plan_key {id}")
        if len(meta.keys) == 0:
            self._weekly_plan_listeners.pop(id)
            self._weeklyplanmap.pop(id)

    async def _fetch_weekly_plans(self, listeners: Dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]):
        request_data_institutions = list[AulaChildProfile]()
        new_weeklyplanmap = dict[int, List[AulaWeeklyPlan]]()
        for (id, meta) in listeners.items():
            if meta.last_updated is None or meta.last_updated < now() - WEEKLY_PLANS_UPDATE_INTERVAL or meta.last_updated.date() < now().date():
                request_data_institutions.append(meta.keys[0])
            else:
                new_weeklyplanmap[id] = self._weeklyplanmap.get(id, list[AulaWeeklyPlan]())

        for institution in request_data_institutions:
            # Aula's answer for several profiles cannot be split between them, so each is read alone.
            weekly_plans = await self._client.get_weekly_plans([institution], now(), now() + SYNC_EVENT_MAX_TIME)
            _LOGGER.debug(f"Fetching weekly_plans for id: {institution.id}, got {len(weekly_plans)} weekly_plans")
            new_weeklyplanmap[institution.id] = weekly_plans

        return (new_weeklyplanmap, [inst.id for inst in request_data_institutions])

    async def get_easyiq_weekly_plans_for_interval(self, profiles: List[AulaChildProfile], start_datetime: datetime.datetime, end_datetime: datetime.datetime) -> List[AulaEasyiqWeeklyPlan]:
        """Aula's EasyIQ weekly plans of the period, read now; none when Aula cannot be read."""
        return await self._read_period("EasyIQ weekly plans", self._client.get_easyiq_weekly_plans(profiles, start_datetime, end_datetime))

    def get_easyiq_weekly_plans(self, profile: AulaChildProfile) -> List[AulaEasyiqWeeklyPlan]:
        """The EasyIQ weekly plans last read for the child."""
        return self._easyiqweekplanmap.get(profile.id, [])

    async def async_add_easyiq_weekplan_key(self, profile: AulaChildProfile) -> None:
        """Follow the child's EasyIQ weekly plans, and read what is due now."""
        id = profile.id
        meta = self._easyiq_weekplan_listeners.setdefault(id, AulaCalendarCoordinatorMeta[AulaChildProfile]())
        _LOGGER.debug(f"async_add_easyiq_weekplan_key {id}")
        meta.keys.append(profile)
        self._easyiqweekplanmap.setdefault(id, [])
        await self.async_refresh()

    async def async_remove_easyiq_weekplan_key(self, profile: AulaChildProfile) -> None:
        """Stop following the child's EasyIQ plans for one calendar; forget them when none follows them."""
        id = profile.id
        meta = self._easyiq_weekplan_listeners.get(id)
        if meta is None: return
        meta.keys.remove(profile)
        _LOGGER.debug(f"async_remove_easyiq_weekplan_key {id}")
        if len(meta.keys) == 0:
            self._easyiq_weekplan_listeners.pop(id)
            self._easyiqweekplanmap.pop(id)

    async def _fetch_easyiq_weekly_plans(self, listeners: Dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]):
        request_data_profiles = list[AulaChildProfile]()
        new_easyiqmap = dict[int, List[AulaEasyiqWeeklyPlan]]()
        for (id, meta) in listeners.items():
            if meta.last_updated is None or meta.last_updated < now() - EASYIQ_WEEKPLAN_UPDATE_INTERVAL or meta.last_updated.date() < now().date():
                request_data_profiles.append(meta.keys[0])
            else:
                new_easyiqmap[id] = self._easyiqweekplanmap.get(id, list[AulaEasyiqWeeklyPlan]())

        for profile in request_data_profiles:
            plans = await self._client.get_easyiq_weekly_plans([profile], now(), now() + SYNC_EVENT_MAX_TIME)
            _LOGGER.debug(f"Fetching easyiq weekplans for id: {profile.id}, got {len(plans)} plans")
            new_easyiqmap[profile.id] = plans

        return (new_easyiqmap, [p.id for p in request_data_profiles])

    async def get_newsletters_for_interval(self, profiles: List[AulaChildProfile], start_datetime: datetime.datetime, end_datetime: datetime.datetime) -> List[AulaWeeklyNewsletter]:
        """Aula's newsletters of the period, read now; none when Aula cannot be read."""
        return await self._read_period("newsletters", self._client.get_newsletters(profiles, start_datetime, end_datetime))

    def get_newsletters(self, profile: AulaChildProfile) -> List[AulaWeeklyNewsletter]:
        """The MinUddannelse newsletters last read for the child."""
        return self._newslettermap.get(profile.id, [])

    async def async_add_newsletter_key(self, profile: AulaChildProfile) -> None:
        """Follow the child's newsletters, and read what is due now."""
        id = profile.id
        meta = self._newsletter_listeners.setdefault(id, AulaCalendarCoordinatorMeta[AulaChildProfile]())
        _LOGGER.debug(f"async_add_newsletter_key {id}")
        meta.keys.append(profile)
        self._newslettermap.setdefault(id, [])
        await self.async_refresh()

    async def async_remove_newsletter_key(self, profile: AulaChildProfile) -> None:
        """Stop following the child's newsletters for one calendar; forget them when none follows them."""
        id = profile.id
        meta = self._newsletter_listeners.get(id)
        if meta is None: return
        meta.keys.remove(profile)
        _LOGGER.debug(f"async_remove_newsletter_key {id}")
        if len(meta.keys) == 0:
            self._newsletter_listeners.pop(id)
            self._newslettermap.pop(id)

    async def _fetch_newsletters(self, listeners: Dict[int, AulaCalendarCoordinatorMeta[AulaChildProfile]]):
        request_data_profiles = list[AulaChildProfile]()
        new_newslettermap = dict[int, List[AulaWeeklyNewsletter]]()
        for (id, meta) in listeners.items():
            if meta.last_updated is None or meta.last_updated < now() - NEWSLETTER_UPDATE_INTERVAL or meta.last_updated.date() < now().date():
                request_data_profiles.append(meta.keys[0])
            else:
                new_newslettermap[id] = self._newslettermap.get(id, list[AulaWeeklyNewsletter]())

        for profile in request_data_profiles:
            newsletters = await self._client.get_newsletters([profile], now(), now() + SYNC_EVENT_MAX_TIME)
            _LOGGER.debug(f"Fetching newsletters for id: {profile.id}, got {len(newsletters)} newsletters")
            new_newslettermap[profile.id] = newsletters

        return (new_newslettermap, [p.id for p in request_data_profiles])
