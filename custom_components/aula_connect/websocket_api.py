"""A read-only websocket API of the entries' children and their days: lessons, events, weekly plan tasks and birthdays."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any, cast

import probatio
from aula_connect.api.models.constants import AulaCalendarEventType
from aula_connect.api.models.module import AulaCalendarEvent, AulaChildProfile
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.const import ERR_INVALID_FORMAT, ERR_NOT_FOUND, WebSocketCommandHandler
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryChange, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import VolDictType
from homeassistant.util import dt as dt_util

from .aula_data import AulaConfigEntry
from .calendar import booked_time_slot, celebrated_on, is_meeting, shown_for
from .const import DOMAIN

MAX_DAYS = 21
"""The most days one subscription covers."""

type Days = tuple[date, date]
"""A first day and the day after the last."""

# Home Assistant's signatures name probatio's All without its type parameter, which a strict
# check takes for an unknown type; these are the types they have.
_websocket_command: Callable[[VolDictType], Callable[[WebSocketCommandHandler], WebSocketCommandHandler]] = getattr(
    websocket_api, "websocket_command"
)
_async_register_command: Callable[[HomeAssistant, WebSocketCommandHandler], None] = getattr(
    websocket_api, "async_register_command"
)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the API's commands."""
    _async_register_command(hass, ws_children)
    _async_register_command(hass, ws_subscribe_week)


def _loaded_entries(hass: HomeAssistant) -> list[AulaConfigEntry]:
    return cast(list[AulaConfigEntry], hass.config_entries.async_loaded_entries(DOMAIN))


CHILDREN_SCHEMA: VolDictType = {probatio.Required("type"): "aula_connect/children"}
WEEK_SCHEMA: VolDictType = {
    probatio.Required("type"): "aula_connect/week/subscribe",
    probatio.Required("entry_id"): str,
    probatio.Required("child_id"): int,
    probatio.Required("start"): cv.date,
    probatio.Required("end"): cv.date,
}


@_websocket_command(CHILDREN_SCHEMA)
@callback
def ws_children(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer with the children of every loaded entry, entry by entry."""
    children = [
        {
            "entry_id": entry.entry_id,
            "child_id": child.id,
            "first_name": child.first_name,
            "name": child.name,
            "institution_name": child.institution_profile.institution_name,
        }
        for entry in _loaded_entries(hass)
        for child in entry.runtime_data.data_coordinator.data.children
    ]
    connection.send_result(msg["id"], {"children": children})


@_websocket_command(WEEK_SCHEMA)
@callback
def ws_subscribe_week(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Send the child's days from `start` to the day before `end`, and again when Aula is read anew for the child."""
    start: date = msg["start"]
    end: date = msg["end"]
    if not start < end <= start + timedelta(days=MAX_DAYS):
        connection.send_error(msg["id"], ERR_INVALID_FORMAT, f"end must be after start, by at most {MAX_DAYS} days")
        return
    entry = next((entry for entry in _loaded_entries(hass) if entry.entry_id == msg["entry_id"]), None)
    child = (
        None
        if entry is None
        else next((child for child in entry.runtime_data.data_coordinator.data.children if child.id == msg["child_id"]), None)
    )
    if entry is None or child is None:
        connection.send_error(msg["id"], ERR_NOT_FOUND, "No such child")
        return

    subscription = _WeekSubscription(hass, connection, msg["id"], entry, child, (start, end))
    connection.subscriptions[msg["id"]] = subscription.async_unsubscribe
    connection.send_result(msg["id"])
    subscription.async_send()


class _WeekSubscription:
    """Sends a child's days to a connection, each time Aula is read anew for the child.

    When the entry is no longer loaded, or the child is gone from it, it sends `{"ended": true}` once
    and stops.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg_id: int,
        entry: AulaConfigEntry,
        child: AulaChildProfile,
        days: Days,
    ) -> None:
        self._hass = hass
        self._connection = connection
        self._msg_id = msg_id
        self._entry = entry
        self._child = child
        self._days = days
        self._task: asyncio.Task[None] | None = None
        self._ended = False
        self._remove_listeners = [
            entry.runtime_data.calendar_coordinator.async_add_listener(self._async_updated),
            entry.runtime_data.data_coordinator.async_add_listener(self._async_children_updated),
            async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._async_entry_changed),
        ]

    @callback
    def async_send(self) -> None:
        """Send the days as they are now, in place of a sending still under way."""
        if self._task is not None:
            self._task.cancel()
        self._task = self._hass.async_create_task(self._send())

    @callback
    def async_unsubscribe(self) -> None:
        """Stop sending; nothing more once stopped."""
        if self._ended:
            return
        self._ended = True
        for remove in self._remove_listeners:
            remove()
        if self._task is not None:
            self._task.cancel()

    @callback
    def _async_end(self) -> None:
        """Say that the subscription has ended, and stop sending."""
        if not self._ended:
            self._connection.send_message(event_message(self._msg_id, {"ended": True}))
            self.async_unsubscribe()

    @callback
    def _async_entry_changed(self, change: ConfigEntryChange, entry: ConfigEntry) -> None:
        # An entry is unloaded before it is removed or reloaded.
        if entry.entry_id == self._entry.entry_id and entry.state is not ConfigEntryState.LOADED:
            self._async_end()

    @callback
    def _async_children_updated(self) -> None:
        coordinator = self._entry.runtime_data.data_coordinator
        if coordinator.last_update_success and all(child.id != self._child.id for child in coordinator.data.children):
            self._async_end()

    async def _send(self) -> None:
        payload = await async_child_days(self._entry, self._child, self._days)
        self._connection.send_message(event_message(self._msg_id, payload))

    @callback
    def _async_updated(self) -> None:
        coordinator = self._entry.runtime_data.calendar_coordinator
        data = coordinator.data
        # The coordinator tells its listeners every poll, though it reads each kind only when it is due.
        if coordinator.last_update_success and (
            self._child.institution_profile.id in data.updated_events_for_listener_keys
            or self._child.id in data.updated_weekly_plans_for_listener_keys
            or self._child.id in data.updated_birthdays_for_listener_keys
        ):
            self.async_send()


def _midnight(day: date) -> datetime:
    return dt_util.start_of_local_day(day)


def _split(days: Days, read: Days | None) -> tuple[Days | None, list[Days]]:
    """The part of `days` within `read`, and the parts of `days` outside it."""
    start, end = days
    if read is None or read[1] <= start or end <= read[0]:
        return None, [days]
    inside = (max(start, read[0]), min(end, read[1]))
    outside = [part for part in ((start, inside[0]), (inside[1], end)) if part[0] < part[1]]
    return inside, outside


async def _sources[T](
    days: Days,
    read: Days | None,
    cached: Callable[[], list[T]],
    read_period: Callable[[datetime, datetime], Awaitable[list[T]]],
) -> list[tuple[Days, list[T]]]:
    """What the coordinator holds for the part of `days` it read, and what Aula says now for the rest, by part."""
    inside, outside = _split(days, read)
    sources = list[tuple[Days, list[T]]]()
    if inside is not None:
        sources.append((inside, cached()))
    for part in outside:
        try:
            items = await read_period(_midnight(part[0]), _midnight(part[1]))
        except ConfigEntryAuthFailed:
            # Logging in again is asked for already; the card shows what there is meanwhile.
            items = list[T]()
        sources.append((part, items))
    return sources


def _overlaps(start: datetime, end: datetime, days: Days) -> bool:
    first, after = _midnight(days[0]), _midnight(days[1])
    return start < after and (end > first or start >= first)


async def async_child_days(entry: AulaConfigEntry, child: AulaChildProfile, days: Days) -> dict[str, Any]:
    """The child's lessons, other events, weekly plan tasks and birthdays from the first day to the day before the last.

    A part Aula cannot be read for is left out.
    """
    lessons, events = await _async_events(entry, child, days)
    tasks: list[dict[str, Any]] = []
    birthdays: list[dict[str, Any]] = []
    # The weekly plan widget brings the birthday calendar too.
    if entry.runtime_data.data_coordinator.weekly_plans_supported():
        tasks = await _async_tasks(entry, child, days)
        birthdays = await _async_birthdays(entry, child, days)
    return {
        "start": days[0].isoformat(),
        "end": days[1].isoformat(),
        "lessons": lessons,
        "events": events,
        "tasks": tasks,
        "birthdays": birthdays,
    }


def _times(child: AulaChildProfile, event: AulaCalendarEvent) -> tuple[datetime, datetime, bool]:
    """When the event is for the child, and whether that is a booked time slot."""
    booked = booked_time_slot(child.institution_profile, event) if is_meeting(event) else None
    if booked is None:
        return event.start_datetime, event.end_datetime, False
    return booked.start_datetime, booked.end_datetime, True


async def _async_events(
    entry: AulaConfigEntry, child: AulaChildProfile, days: Days
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    coordinator = entry.runtime_data.calendar_coordinator
    institution = child.institution_profile
    sources = await _sources(
        days,
        coordinator.events_read_for(institution),
        lambda: coordinator.get_events(institution),
        lambda start, end: coordinator.get_events_for_interval([institution], start, end),
    )
    lessons = list[tuple[datetime, datetime, dict[str, Any]]]()
    events = list[tuple[datetime, datetime, dict[str, Any]]]()
    seen = set[int]()
    for part, items in sources:
        for event in items:
            if event.id in seen or not shown_for(child, institution, event):
                continue
            start, end, booked = _times(child, event)
            if not _overlaps(start, end, part):
                continue
            seen.add(event.id)
            location = event.primary_resource.name if event.primary_resource is not None else None
            if event.type == AulaCalendarEventType.LESSON:
                lesson = event.lesson
                lessons.append((start, end, {
                    "id": str(event.id),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "title": event.title,
                    "teachers": [p.teacher_name for p in lesson.participants if p.teacher_name] if lesson else [],
                    "location": location,
                    "status": lesson.lesson_status if lesson else None,
                }))
            else:
                if booked and location is None:
                    location = institution.institution_name
                events.append((start, end, {
                    "id": str(event.id),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "all_day": event.all_day,
                    "title": event.title,
                    "type": event.type,
                    "location": location,
                }))
    return (
        [item for _, _, item in sorted(lessons, key=lambda item: (item[0], item[1]))],
        [item for _, _, item in sorted(events, key=lambda item: (item[0], item[1]))],
    )


async def _async_tasks(entry: AulaConfigEntry, child: AulaChildProfile, days: Days) -> list[dict[str, Any]]:
    coordinator = entry.runtime_data.calendar_coordinator
    sources = await _sources(
        days,
        coordinator.weekly_plans_read_for(child),
        lambda: coordinator.get_weekly_plans(child),
        lambda start, end: coordinator.get_weekly_plans_for_interval([child], start, end),
    )
    tasks = list[tuple[date, dict[str, Any]]]()
    for (first, after), plans in sources:
        for plan in plans:
            for daily in plan.daily_plans:
                if not first <= daily.date < after:
                    continue
                for task in daily.tasks:
                    tasks.append((daily.date, {
                        "id": str(task.id),
                        "date": daily.date.isoformat(),
                        "type": task.type,
                        "title": task.title,
                        "content": task.content,
                        "subject": task.pill,
                        "group": task.group,
                        "author": task.author,
                    }))
    return [item for _, item in sorted(tasks, key=lambda item: item[0])]


async def _async_birthdays(entry: AulaConfigEntry, child: AulaChildProfile, days: Days) -> list[dict[str, Any]]:
    coordinator = entry.runtime_data.calendar_coordinator
    sources = await _sources(
        days,
        coordinator.birthdays_read_for(child),
        lambda: coordinator.get_birthdays(child),
        lambda start, end: coordinator.get_birthdays_for_interval([child], start, end),
    )
    birthdays = list[tuple[date, str]]()
    for (first, after), items in sources:
        for birthday in items:
            for year in range(first.year, after.year + 1):
                day = celebrated_on(birthday, year)
                if first <= day < after:
                    birthdays.append((day, birthday.full_name))
    return [{"date": day.isoformat(), "name": name} for day, name in sorted(birthdays)]
