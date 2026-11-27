"""The websocket API of the entries' children: their days - lessons, events, weekly plan tasks and
birthdays - and what Aula tells of one event alone; their day today; and what a guardian does in
Aula for them, each on the user's explicit action: report sick, register a vacation, answer a
vacation request, and reply in a thread."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any, cast

import probatio
from aula_connect.api.models.constants import AulaCalendarEventType
from aula_connect.api.models.module import AulaCalendarEvent, AulaChildProfile, AulaEventDetail, AulaVacationDay
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.const import (
    ERR_HOME_ASSISTANT_ERROR,
    ERR_INVALID_FORMAT,
    ERR_NOT_FOUND,
    ERR_NOT_SUPPORTED,
    WebSocketCommandHandler,
)
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryChange, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import VolDictType
from homeassistant.util import dt as dt_util

from . import family_actions
from .aula_data import AulaConfigEntry
from .calendar import booked_time_slot, celebrated_on, is_meeting, shown_for
from .const import DOMAIN
from .entity import child_unique_id
from .family_actions import AulaError, FamilyActionError, InvalidError, NotAllowedError, NotFoundError, NotSupportedError
from .redact import error_text

MAX_DAYS = 21
"""The most days one subscription covers."""

MAX_HOURS_DAYS = 62
"""The most days the opening hours are asked for at once."""

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

ERR_NOT_ALLOWED = "not_allowed"
"""The error of a change in Aula the entry's options do not allow."""
_async_response: Callable[
    [Callable[[HomeAssistant, ActiveConnection, dict[str, Any]], Awaitable[None]]], WebSocketCommandHandler
] = getattr(websocket_api, "async_response")


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the API's commands."""
    _async_register_command(hass, ws_children)
    _async_register_command(hass, ws_subscribe_week)
    _async_register_command(hass, ws_event)
    _async_register_command(hass, ws_subscribe_today)
    _async_register_command(hass, ws_report_sick)
    _async_register_command(hass, ws_undo_sick)
    _async_register_command(hass, ws_check_sick)
    _async_register_command(hass, ws_threads)
    _async_register_command(hass, ws_reply)
    _async_register_command(hass, ws_vacations)
    _async_register_command(hass, ws_posts)
    _async_register_command(hass, ws_opening_hours)
    _async_register_command(hass, ws_add_vacation)
    _async_register_command(hass, ws_delete_vacation)
    _async_register_command(hass, ws_post_seen)
    _async_register_command(hass, ws_vacation_requests)
    _async_register_command(hass, ws_answer_vacation_request)


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

EVENT_SCHEMA: VolDictType = {
    probatio.Required("type"): "aula_connect/event",
    probatio.Required("entry_id"): str,
    probatio.Required("child_id"): int,
    probatio.Required("event_id"): int,
}


def _child_schema(command: str, **fields: Any) -> VolDictType:
    """The schema of a command about one child of an entry, with its further fields."""
    return {
        probatio.Required("type"): command,
        probatio.Required("entry_id"): str,
        probatio.Required("child_id"): int,
        **{probatio.Required(name): value for name, value in fields.items()},
    }


TODAY_SCHEMA = _child_schema("aula_connect/today/subscribe")
REPORT_SICK_SCHEMA = _child_schema("aula_connect/sick/report")
UNDO_SICK_SCHEMA = _child_schema("aula_connect/sick/undo")
CHECK_SICK_SCHEMA = _child_schema("aula_connect/sick/check")
THREADS_SCHEMA = _child_schema("aula_connect/threads")
REPLY_SCHEMA = _child_schema("aula_connect/message/reply", thread_id=int, text=str)
VACATIONS_SCHEMA = _child_schema("aula_connect/vacations")
POSTS_SCHEMA: VolDictType = {probatio.Required("type"): "aula_connect/posts", probatio.Required("entry_id"): str}
OPENING_HOURS_SCHEMA = _child_schema("aula_connect/opening_hours", start=cv.date, end=cv.date)
ADD_VACATION_SCHEMA: VolDictType = {
    probatio.Required("type"): "aula_connect/vacation/add",
    probatio.Required("entry_id"): str,
    probatio.Required("child_ids"): [int],
    probatio.Required("start"): cv.date,
    probatio.Required("end"): cv.date,
    probatio.Optional("comment", default=""): str,
}
# A command's own "id" is the message's, so the vacation and the request are named apart.
DELETE_VACATION_SCHEMA: VolDictType = {
    probatio.Required("type"): "aula_connect/vacation/delete",
    probatio.Required("entry_id"): str,
    probatio.Required("vacation_id"): int,
}
POST_SEEN_SCHEMA: VolDictType = {
    probatio.Required("type"): "aula_connect/post/seen",
    probatio.Required("entry_id"): str,
    probatio.Required("post_id"): int,
}
VACATION_REQUESTS_SCHEMA = _child_schema("aula_connect/vacation_requests")
DAY_SCHEMA: VolDictType = {
    probatio.Required("date"): cv.date,
    probatio.Required("is_coming"): bool,
    probatio.Optional("entry_time"): probatio.Any(None, cv.time),
    probatio.Optional("exit_time"): probatio.Any(None, cv.time),
}
ANSWER_VACATION_REQUEST_SCHEMA: VolDictType = {
    **_child_schema("aula_connect/vacation_request/answer", request_id=int, days=[DAY_SCHEMA]),
    probatio.Optional("comment", default=""): str,
}


def vacation_days(days: list[dict[str, Any]]) -> list[AulaVacationDay]:
    """The days of an answer to a vacation request, as validated by DAY_SCHEMA."""
    return [AulaVacationDay(day["date"], day["is_coming"], day.get("entry_time"), day.get("exit_time")) for day in days]


@_websocket_command(CHILDREN_SCHEMA)
@callback
def ws_children(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer with the children of every loaded entry, entry by entry, with their picture and the entry's guardian."""
    registry = er.async_get(hass)
    children = list[dict[str, Any]]()
    for entry in _loaded_entries(hass):
        coordinator = entry.runtime_data.data_coordinator
        data = coordinator.data
        guardian = data.profiles[0].name if data.profiles else entry.title
        for child in data.children:
            children.append({
                "entry_id": entry.entry_id,
                "child_id": child.id,
                "first_name": child.first_name,
                "name": child.name,
                "institution_name": child.institution_profile.institution_name,
                "picture_entity": registry.async_get_entity_id("image", DOMAIN, child_unique_id(coordinator, "picture", child)),
                "guardian": guardian,
                "allowed": family_actions.allowed(entry),
            })
    connection.send_result(msg["id"], {"children": children})


async def _respond(connection: ActiveConnection, msg: dict[str, Any], work: Callable[[], Awaitable[dict[str, Any]]]) -> None:
    """Answer with what `work` gives, or with the error that tells why it could not be done."""
    try:
        result = await work()
    except NotFoundError as err:
        connection.send_error(msg["id"], ERR_NOT_FOUND, str(err))
    except InvalidError as err:
        connection.send_error(msg["id"], ERR_INVALID_FORMAT, str(err))
    except NotSupportedError as err:
        connection.send_error(msg["id"], ERR_NOT_SUPPORTED, str(err))
    except NotAllowedError as err:
        connection.send_error(msg["id"], ERR_NOT_ALLOWED, str(err))
    except AulaError as err:
        connection.send_error(msg["id"], ERR_HOME_ASSISTANT_ERROR, str(err))
    else:
        connection.send_result(msg["id"], result)


def _entry_and_child(hass: HomeAssistant, msg: dict[str, Any]) -> tuple[AulaConfigEntry, AulaChildProfile]:
    entry = family_actions.loaded_entry(hass, msg["entry_id"])
    return entry, family_actions.entry_child(entry, msg["child_id"])


@_websocket_command(REPORT_SICK_SCHEMA)
@_async_response
async def ws_report_sick(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Report the child sick today, the way Aula offers for it."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        return {"route": await family_actions.report_sick(hass, entry, child)}

    await _respond(connection, msg, work)


@_websocket_command(CHECK_SICK_SCHEMA)
@_async_response
async def ws_check_sick(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Tell whether the child can be reported sick from here, and where, sending nothing."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        return await family_actions.check_sick_report(hass, entry, child)

    await _respond(connection, msg, work)


@_websocket_command(UNDO_SICK_SCHEMA)
@_async_response
async def ws_undo_sick(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Take back today's report that the child is sick."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        return {"route": await family_actions.undo_sick(hass, entry, child)}

    await _respond(connection, msg, work)


@_websocket_command(THREADS_SCHEMA)
@_async_response
async def ws_threads(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer with the threads about the child, and the one remembered for it."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        return await family_actions.threads(hass, entry, child)

    await _respond(connection, msg, work)


@_websocket_command(REPLY_SCHEMA)
@_async_response
async def ws_reply(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Reply in a thread about the child, and remember the thread for it."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        await family_actions.reply(hass, entry, child, msg["thread_id"], msg["text"])
        return {}

    await _respond(connection, msg, work)


@_websocket_command(POSTS_SCHEMA)
@_async_response
async def ws_posts(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer with the newest posts of the entry's news feed."""

    async def work() -> dict[str, Any]:
        return await family_actions.posts(hass, family_actions.loaded_entry(hass, msg["entry_id"]))

    await _respond(connection, msg, work)


@_websocket_command(OPENING_HOURS_SCHEMA)
@_async_response
async def ws_opening_hours(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer with the days the child's institution is closed, and its hours from `start` to `end`."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        start, end = msg["start"], msg["end"]
        if end < start or (end - start).days > MAX_HOURS_DAYS:
            reason = f"the period must run forward and be at most {MAX_HOURS_DAYS} days"
            raise InvalidError("invalid_request", f"Not read: {reason}", {"reason": reason})
        return await family_actions.opening_hours(hass, entry, child, start, end)

    await _respond(connection, msg, work)


@_websocket_command(VACATIONS_SCHEMA)
@_async_response
async def ws_vacations(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer with the vacations registered for the child."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        return await family_actions.vacations(hass, entry, child)

    await _respond(connection, msg, work)


@_websocket_command(ADD_VACATION_SCHEMA)
@_async_response
async def ws_add_vacation(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Register a vacation for children of one entry."""

    async def work() -> dict[str, Any]:
        entry = family_actions.loaded_entry(hass, msg["entry_id"])
        children = [family_actions.entry_child(entry, child_id) for child_id in msg["child_ids"]]
        await family_actions.add_vacation(hass, entry, children, msg["start"], msg["end"], msg["comment"])
        return {}

    await _respond(connection, msg, work)


@_websocket_command(POST_SEEN_SCHEMA)
@_async_response
async def ws_post_seen(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Tell Aula a post of an entry's news feed has been seen."""

    async def work() -> dict[str, Any]:
        entry = family_actions.loaded_entry(hass, msg["entry_id"])
        return await family_actions.mark_post_seen(hass, entry, msg["post_id"])

    await _respond(connection, msg, work)


@_websocket_command(DELETE_VACATION_SCHEMA)
@_async_response
async def ws_delete_vacation(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Delete a vacation of one of an entry's children."""

    async def work() -> dict[str, Any]:
        entry = family_actions.loaded_entry(hass, msg["entry_id"])
        await family_actions.delete_vacation(hass, entry, msg["vacation_id"])
        return {}

    await _respond(connection, msg, work)


@_websocket_command(VACATION_REQUESTS_SCHEMA)
@_async_response
async def ws_vacation_requests(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer with the child's vacation requests, day by day."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        return await family_actions.vacation_requests(hass, entry, child)

    await _respond(connection, msg, work)


@_websocket_command(ANSWER_VACATION_REQUEST_SCHEMA)
@_async_response
async def ws_answer_vacation_request(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer one of the child's vacation requests, day by day."""

    async def work() -> dict[str, Any]:
        entry, child = _entry_and_child(hass, msg)
        await family_actions.answer_vacation_request(hass, entry, child, msg["request_id"], vacation_days(msg["days"]), msg["comment"])
        return {}

    await _respond(connection, msg, work)


@_websocket_command(TODAY_SCHEMA)
@callback
def ws_subscribe_today(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Send the child's day today, and again each time the entry's data is read anew."""
    try:
        entry, child = _entry_and_child(hass, msg)
    except FamilyActionError:
        connection.send_error(msg["id"], ERR_NOT_FOUND, "No such child")
        return
    subscription = _TodaySubscription(hass, connection, msg["id"], entry, child)
    connection.subscriptions[msg["id"]] = subscription.async_unsubscribe
    connection.send_result(msg["id"])
    subscription.async_send()


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


@_websocket_command(EVENT_SCHEMA)
@_async_response
async def ws_event(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer with what Aula tells of one event alone, as it concerns the child and its guardian.

    Of those invited, only how many there are and how many answered are told, not who.
    """
    entry = next((entry for entry in _loaded_entries(hass) if entry.entry_id == msg["entry_id"]), None)
    data = None if entry is None else entry.runtime_data.data_coordinator.data
    child = None if data is None else next((child for child in data.children if child.id == msg["child_id"]), None)
    if entry is None or data is None or child is None:
        connection.send_error(msg["id"], ERR_NOT_FOUND, "No such child")
        return
    try:
        detail = await entry.runtime_data.client.get_event(msg["event_id"])
    except Exception as err:
        connection.send_error(msg["id"], ERR_HOME_ASSISTANT_ERROR, f"The event could not be read: {error_text(err)}")
        return
    if detail is None:
        connection.send_error(msg["id"], ERR_NOT_FOUND, "No such event")
        return
    own = [institution.id for profile in data.profiles for institution in profile.institution_profiles]
    connection.send_result(msg["id"], event_details(detail, child, own))


def event_details(detail: AulaEventDetail, child: AulaChildProfile, own: list[int]) -> dict[str, Any]:
    """What the event tells, as it concerns the child and the guardian's institution profiles `own`."""
    first, after = all_day_dates(detail.start_datetime, detail.end_datetime) if detail.all_day else (detail.start_datetime, detail.end_datetime)
    invitation = detail.invitation_of(own)
    booking = detail.booking_for(child.id)
    return {
        "id": str(detail.id),
        "title": detail.title,
        "type": detail.type,
        "all_day": detail.all_day,
        "start": first.isoformat(),
        "end": after.isoformat(),
        "description": detail.description_html,
        "location": detail.location,
        "institution_name": detail.institution_name,
        "creator": {"name": detail.creator_name, "role": detail.creator_role} if detail.creator_name else None,
        "co_organizers": detail.co_organizer_names,
        "invited_groups": detail.invited_group_names,
        "invited": len(detail.invitations),
        "answered": sum(1 for invitation in detail.invitations if invitation.response != "waiting"),
        "response_required": detail.response_required,
        "response_deadline": detail.response_deadline.isoformat() if detail.response_deadline else None,
        "deadline_passed": detail.deadline_exceeded,
        "answer": invitation.response if invitation else None,
        "answered_at": invitation.answered_at.isoformat() if invitation and invitation.answered_at else None,
        "booked": {
            "start": booking.time.start_datetime.isoformat(),
            "end": booking.time.end_datetime.isoformat(),
            "location": booking.location,
        } if booking else None,
        "attachments": detail.attachment_count,
        "private": detail.private,
        "deleted": detail.deleted,
    }


class _ChildSubscription(ABC):
    """Sends what concerns a child to a connection, until the entry is no longer loaded or the child is gone from it.

    Then it sends `{"ended": true}` once and stops.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg_id: int,
        entry: AulaConfigEntry,
        child: AulaChildProfile,
    ) -> None:
        self._hass = hass
        self._connection = connection
        self._msg_id = msg_id
        self._entry = entry
        self._child = child
        self._task: asyncio.Task[None] | None = None
        self._ended = False
        self._remove_listeners = [
            entry.runtime_data.data_coordinator.async_add_listener(self._async_children_updated),
            async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._async_entry_changed),
        ]

    @callback
    def async_send(self) -> None:
        """Send what concerns the child now, in place of a sending still under way."""
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
        else:
            self._async_data_updated()

    @callback
    def _async_data_updated(self) -> None:
        """Take in a read of the entry's data coordinator."""

    async def _send(self) -> None:
        self._connection.send_message(event_message(self._msg_id, await self._payload()))

    @abstractmethod
    async def _payload(self) -> dict[str, Any]:
        """What is sent of the child."""


class _TodaySubscription(_ChildSubscription):
    """Sends a child's day today each time the entry's data is read anew."""

    @callback
    def _async_data_updated(self) -> None:
        if self._entry.runtime_data.data_coordinator.last_update_success:
            self.async_send()

    async def _payload(self) -> dict[str, Any]:
        return family_actions.today(self._entry, self._child)


class _WeekSubscription(_ChildSubscription):
    """Sends a child's days each time Aula is read anew for the child."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg_id: int,
        entry: AulaConfigEntry,
        child: AulaChildProfile,
        days: Days,
    ) -> None:
        super().__init__(hass, connection, msg_id, entry, child)
        self._days = days
        self._remove_listeners.append(entry.runtime_data.calendar_coordinator.async_add_listener(self._async_updated))

    async def _payload(self) -> dict[str, Any]:
        return await async_child_days(self._entry, self._child, self._days)

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
    week_plan = entry.runtime_data.data_coordinator.weekly_plans_supported()
    if week_plan:
        tasks = await _async_tasks(entry, child, days)
        birthdays = await _async_birthdays(entry, child, days)
    return {
        "start": days[0].isoformat(),
        "end": days[1].isoformat(),
        "week_plan": week_plan,
        "lessons": lessons,
        "events": events,
        "tasks": tasks,
        "birthdays": birthdays,
    }


def all_day_dates(start: datetime, end: datetime) -> tuple[date, date]:
    """The local days an all-day event covers: the first, and the day after the last."""
    first = dt_util.as_local(start).date()
    last = dt_util.as_local(end)
    after = last.date() if last.time() == time(0) else last.date() + timedelta(days=1)
    return first, max(after, first + timedelta(days=1))


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
                first, after = all_day_dates(start, end) if event.all_day else (start, end)
                events.append((start, end, {
                    "id": str(event.id),
                    "start": first.isoformat(),
                    "end": after.isoformat(),
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
