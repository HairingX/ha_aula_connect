"""What a guardian does in Aula through Home Assistant - report a child sick, register a vacation,
answer a vacation request, reply in a thread - and what is read for it.

Each write is sent once, on the user's explicit action only, and never again by itself: where it
fails, it may or may not have been done, and the error says so.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, cast

from aula_connect.api.aula_errors import (
    AulaCredentialError,
    NoSickReportRouteError,
    SickReportFinalError,
)
from aula_connect.tabulex import TabulexError
from aula_connect.api.models.module import (
    AulaChildProfile,
    AulaMessageThread,
    AulaPresenceStatus,
    AulaSickReportRoute,
    AulaVacationDay,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .aula_data import AulaConfigEntry
from .aula_data_coordinator import pending_vacation_requests
from .const import (
    ALLOWED_CHANGES,
    CONF_ALLOW_MESSAGES,
    CONF_ALLOW_SICK_REPORTS,
    CONF_ALLOW_VACATION_REQUESTS,
    CONF_ALLOW_VACATIONS,
    DOMAIN,
)
from .family import previous_school_day, school_year_start
from .redact import error_text


class FamilyActionError(Exception):
    """An action that could not be done, with the translation that tells why.

    Attributes:
        translation_key: The key of the text under the integration's exceptions.
        placeholders: The values the text names.
    """

    def __init__(self, translation_key: str, message: str, placeholders: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.translation_key = translation_key
        self.placeholders = placeholders or {}


class NotFoundError(FamilyActionError):
    """The entry, child, thread or request asked for is not there."""


class InvalidError(FamilyActionError):
    """What was asked breaks one of Aula's rules, and was not sent."""


class NotSupportedError(FamilyActionError):
    """What was asked is done another way, which Aula Connect cannot use."""


class NotAllowedError(FamilyActionError):
    """What was asked changes Aula in a way the entry's options do not allow."""


class AulaError(FamilyActionError):
    """Aula refused, or could not be reached; a write may or may not have been done."""


def allowed(entry: AulaConfigEntry) -> list[str]:
    """The kinds of change in Aula the entry's options allow, by the names the cards know them by."""
    return [name for option, name in ALLOWED_CHANGES.items() if entry.options.get(option) is True]


def _require(entry: AulaConfigEntry, option: str) -> None:
    """Refuse a change of the kind `option` allows where the entry's options do not allow it.

    Raises:
        NotAllowedError: The option is off.
    """
    if entry.options.get(option) is not True:
        raise NotAllowedError(f"{ALLOWED_CHANGES[option]}_not_allowed", f"The entry's options do not allow {ALLOWED_CHANGES[option].replace('_', ' ')}")


def loaded_entry(hass: HomeAssistant, entry_id: str) -> AulaConfigEntry:
    """The loaded Aula Connect entry `entry_id`.

    Raises:
        NotFoundError: No such entry is loaded.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        raise NotFoundError("entry_not_loaded", "The Aula Connect entry is not loaded")
    return cast(AulaConfigEntry, entry)


def entry_child(entry: AulaConfigEntry, child_id: int) -> AulaChildProfile:
    """The entry's child `child_id`.

    Raises:
        NotFoundError: The entry has no such child.
    """
    for child in entry.runtime_data.data_coordinator.data.children:
        if child.id == child_id:
            return child
    raise NotFoundError("child_not_found", "No such child", {"child_id": str(child_id)})


async def _aula[T](hass: HomeAssistant, entry: AulaConfigEntry, call: Callable[[], Awaitable[T]], write: bool) -> T:
    """What `call` gives, its errors told as the actions tell them.

    Raises:
        InvalidError: The library refused it before sending anything.
        NotSupportedError: Aula offers no way to report the child sick, or its report cannot be
            taken back.
        AulaError: Aula or Tabulex refused it or could not be reached; a login is asked for
            where Aula refused it.
    """
    try:
        return await call()
    except SickReportFinalError as err:
        raise NotSupportedError(
            "sick_report_final", f"The child is reported sick through {err.route}, which takes no report back", {"route": err.route}
        ) from None
    except TabulexError as err:
        text = error_text(err)
        if write:
            raise AulaError("tabulex_write_failed", f"Tabulex did not confirm it, and may or may not have done it: {text}", {"error": text}) from None
        raise AulaError("tabulex_failed", f"Tabulex could not be read: {text}", {"error": text}) from None
    except NoSickReportRouteError:
        raise NotSupportedError("no_sick_route", "Aula offers no way to report the child sick") from None
    except AulaCredentialError:
        entry.async_start_reauth(hass)
        raise AulaError("login_expired", "The login to Aula has expired. Log in with MitID again.") from None
    except ValueError as err:
        raise InvalidError("invalid_request", f"Not sent: {error_text(err)}", {"reason": error_text(err)}) from None
    except Exception as err:
        # Not chained: the error's own text can hold the access token, which a traceback would log.
        text = error_text(err)
        if write:
            raise AulaError("write_failed", f"Aula did not confirm it, and may or may not have done it: {text}", {"error": text}) from None
        raise AulaError("cannot_connect", f"Aula could not be read: {text}", {"error": text}) from None


def _clock(value: time | None) -> str | None:
    return None if value is None else value.strftime("%H:%M")


def today(entry: AulaConfigEntry, child: AulaChildProfile) -> dict[str, Any]:
    """The child's presence today, whether it is sick and since when, and its vacation requests to answer."""
    runtime = entry.runtime_data
    data = runtime.data_coordinator.data
    overview = next((found for found in data.daily_overviews if found.institution_profile.id == child.id), None)
    day = dt_util.now().date()
    sick_today = overview.status == AulaPresenceStatus.SICK if overview is not None else runtime.memory.was_sick(child.id, day)
    since = runtime.memory.sick_since(child.id, day, sick_today)
    return {
        "presence": None if overview is None else {
            "status": overview.status,
            "check_in": _clock(overview.check_in_time),
            "check_out": _clock(overview.check_out_time),
            "planned_in": _clock(overview.check_in_time_expected),
            "planned_out": _clock(overview.check_out_time_expected),
            "exit_with": overview.exit_with or None,
        },
        "sick": {
            "route": data.sick_routes.get(child.id, AulaSickReportRoute.NONE).value,
            "today": sick_today,
            "since": None if since is None else since.isoformat(),
            "wrote_yesterday": runtime.memory.wrote_on(child.id, previous_school_day(day)),
        },
        "vacation_requests_pending": len(pending_vacation_requests(data, child)),
    }


async def report_sick(hass: HomeAssistant, entry: AulaConfigEntry, child: AulaChildProfile) -> str:
    """Report the child sick today, the way Aula offers; the way it was reported."""
    _require(entry, CONF_ALLOW_SICK_REPORTS)
    runtime = entry.runtime_data
    route = await _aula(hass, entry, lambda: runtime.client.report_sick(child), write=True)
    runtime.memory.note_sick(child.id, dt_util.now().date())
    await runtime.data_coordinator.async_request_refresh()
    return route.value


async def undo_sick(hass: HomeAssistant, entry: AulaConfigEntry, child: AulaChildProfile) -> str:
    """Take back today's report that the child is sick; the way it was taken back."""
    _require(entry, CONF_ALLOW_SICK_REPORTS)
    runtime = entry.runtime_data
    await _aula(hass, entry, lambda: runtime.client.undo_sick(child), write=True)
    runtime.memory.forget_sick(child.id, dt_util.now().date())
    await runtime.data_coordinator.async_request_refresh()
    return AulaSickReportRoute.AULA.value


def _family(hass: HomeAssistant) -> set[str]:
    """The names of the guardians of every loaded entry."""
    entries = cast(list[AulaConfigEntry], hass.config_entries.async_loaded_entries(DOMAIN))
    return {profile.name for entry in entries for profile in entry.runtime_data.data_coordinator.data.profiles}


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _last_activity(thread: AulaMessageThread) -> datetime:
    return _aware(thread.latest_message.send_datetime if thread.latest_message is not None else thread.started_datetime)


def _can_reply(thread: AulaMessageThread) -> bool:
    return not thread.is_thread_or_subscription_deleted


async def threads(hass: HomeAssistant, entry: AulaConfigEntry, child: AulaChildProfile) -> dict[str, Any]:
    """The threads about the child, newest first, with the one remembered for it and whether it is to be asked again."""
    runtime = entry.runtime_data
    found = await _aula(hass, entry, lambda: runtime.client.threads_about(child), write=False)
    family = _family(hass)
    remembered = runtime.memory.thread(child.id)
    remembered_id = None if remembered is None else remembered["id"]
    thread = next((thread for thread in found if thread.id == remembered_id), None)
    year = dt_util.start_of_local_day(school_year_start(dt_util.now().date()))
    stale = remembered_id is not None and (thread is None or _aware(thread.started_datetime) < year or not _can_reply(thread))
    return {
        "threads": [
            {
                "thread_id": thread.id,
                "subject": thread.subject,
                "last_activity": _last_activity(thread).isoformat(),
                "employees": [recipient.full_name for recipient in thread.recipients if recipient.full_name not in family],
                "can_reply": _can_reply(thread),
            }
            for thread in sorted(found, key=_last_activity, reverse=True)
        ],
        "remembered": remembered_id,
        "remembered_stale": stale,
    }


async def reply(hass: HomeAssistant, entry: AulaConfigEntry, child: AulaChildProfile, thread_id: int, text: str) -> None:
    """Reply in a thread about the child, and remember it as the child's.

    Raises:
        NotFoundError: The thread is not one about the child that can be replied in.
    """
    _require(entry, CONF_ALLOW_MESSAGES)
    runtime = entry.runtime_data
    found = await _aula(hass, entry, lambda: runtime.client.threads_about(child), write=False)
    if not any(thread.id == thread_id and _can_reply(thread) for thread in found):
        raise NotFoundError("thread_not_found", "No thread about the child can be replied in with that id", {"thread_id": str(thread_id)})
    await _aula(hass, entry, lambda: runtime.client.reply(thread_id, text), write=True)
    runtime.memory.note_wrote(child.id, thread_id, dt_util.now())
    runtime.data_coordinator.note_own_reply(thread_id)


async def vacations(hass: HomeAssistant, entry: AulaConfigEntry, child: AulaChildProfile) -> dict[str, Any]:
    """The vacations registered for the child."""
    found = await _aula(hass, entry, lambda: entry.runtime_data.client.get_vacations([child]), write=False)
    return {
        "vacations": [
            {
                "id": vacation.id,
                "start": vacation.start_date.isoformat(),
                "end": vacation.end_date.isoformat(),
                "comment": vacation.description,
                "editable": vacation.is_editable,
            }
            for vacation in found
            if vacation.child_id == child.id
        ]
    }


async def add_vacation(
    hass: HomeAssistant, entry: AulaConfigEntry, children: list[AulaChildProfile], start: date, end: date, comment: str
) -> None:
    """Register a vacation for the children, `start` to `end` included."""
    _require(entry, CONF_ALLOW_VACATIONS)
    await _aula(hass, entry, lambda: entry.runtime_data.client.add_vacation(children, start, end, comment), write=True)


async def delete_vacation(hass: HomeAssistant, entry: AulaConfigEntry, vacation_id: int) -> None:
    """Delete a vacation of one of the entry's children."""
    _require(entry, CONF_ALLOW_VACATIONS)
    await _aula(hass, entry, lambda: entry.runtime_data.client.delete_vacation(vacation_id), write=True)


def _weekdays(start: date, end: date) -> list[date]:
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    return [day for day in days if day.weekday() < 5]


async def vacation_requests(hass: HomeAssistant, entry: AulaConfigEntry, child: AulaChildProfile) -> dict[str, Any]:
    """The child's vacation requests, each with every weekday of its period as answered, or not yet."""
    client = entry.runtime_data.client
    found = await _aula(hass, entry, lambda: client.get_vacation_requests([child]), write=False)
    requests = list[dict[str, Any]]()
    for request in found:
        if request.child_id != child.id:
            continue
        response_id = request.response_id
        detail = await _aula(hass, entry, lambda: client.get_vacation_request(response_id), write=False)
        answered = {} if detail is None else {day.day: day for day in detail.days}
        times = request.is_presence_times_required if detail is None else detail.is_presence_times_required
        days = list[dict[str, Any]]()
        for day in _weekdays(request.start_date, request.end_date):
            answer = answered.get(day)
            days.append({
                "date": day.isoformat(),
                "is_coming": None if answer is None else answer.is_coming,
                "entry_time": None if answer is None else _clock(answer.entry_time),
                "exit_time": None if answer is None else _clock(answer.exit_time),
            })
        requests.append({
            "id": response_id,
            "title": request.title,
            "start": request.start_date.isoformat(),
            "end": request.end_date.isoformat(),
            "deadline": None if request.response_deadline is None else request.response_deadline.isoformat(),
            "missing_answer": request.missing_answer,
            "note": request.note_to_guardian,
            "times_required": bool(times),
            "days": days,
        })
    return {"requests": requests}


async def answer_vacation_request(
    hass: HomeAssistant,
    entry: AulaConfigEntry,
    child: AulaChildProfile,
    response_id: int,
    days: Sequence[AulaVacationDay],
    comment: str,
) -> None:
    """Answer the child's vacation request day by day.

    Raises:
        NotFoundError: The child has no such request.
    """
    _require(entry, CONF_ALLOW_VACATION_REQUESTS)
    client = entry.runtime_data.client
    detail = await _aula(hass, entry, lambda: client.get_vacation_request(response_id), write=False)
    if detail is None or detail.child_id != child.id:
        raise NotFoundError("request_not_found", "The child has no vacation request with that id", {"request_id": str(response_id)})
    await _aula(hass, entry, lambda: client.answer_vacation_request(response_id, days, comment), write=True)
    await entry.runtime_data.data_coordinator.async_request_refresh()


async def probe_widgets(hass: HomeAssistant, entry: AulaConfigEntry, reads: list[str]) -> dict[str, Any]:
    """Follow once how each widget with a login of its own logs the guardian in, read what its service names and `reads`, and tell what was found; no value of a secret."""
    # The library follows each chain on a session of its own, closed after it: a service's cookies are not Aula's.
    report = await _aula(hass, entry, lambda: entry.runtime_data.client.probe_widgets(reads=reads), write=False)
    return report.as_dict()
