"""The integration's actions: read one method of Aula's API, with who someone is replaced; what a
guardian does in Aula - report a child sick, register a vacation, answer a vacation request,
reply in a thread - each sent once, when it is called; and a probe of the services Aula's widgets log in to."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from aula_connect.api.aula_errors import MethodRefusedError
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import VolDictType

from . import family_actions
from .aula_data import AulaConfigEntry
from .const import DOMAIN
from .diagnostics import pseudonym_key
from .family_actions import AulaError, FamilyActionError
from .redact import error_text
from .websocket_api import vacation_days

SERVICE_READ = "read"
SERVICE_REPORT_SICK = "report_sick"
SERVICE_UNDO_SICK = "undo_sick"
SERVICE_CHECK_SICK_REPORT = "check_sick_report"
SERVICE_ADD_VACATION = "add_vacation"
SERVICE_DELETE_VACATION = "delete_vacation"
SERVICE_ANSWER_VACATION_REQUEST = "answer_vacation_request"
SERVICE_SEND_MESSAGE = "send_message"
SERVICE_PROBE_WIDGETS = "probe_widgets"

READ_SCHEMA = probatio.Schema(
    {
        probatio.Required("config_entry_id"): cv.string,
        probatio.Required("method"): cv.string,
        probatio.Optional("params", default={}): {cv.string: cv.string},
        probatio.Optional("reveal", default=False): probatio.Any(cv.boolean, [cv.string]),
        probatio.Optional("body"): dict,
    }
)


def _schema(fields: VolDictType) -> probatio.Schema:
    """The schema of an action of an entry, with its further fields."""
    return probatio.Schema({probatio.Required("config_entry_id"): cv.string, **fields})


CHILD_SCHEMA = _schema({probatio.Required("child_id"): cv.positive_int})
ADD_VACATION_SCHEMA = _schema(
    {
        probatio.Required("child_ids"): probatio.All(cv.ensure_list, [cv.positive_int]),
        probatio.Required("start"): cv.date,
        probatio.Required("end"): cv.date,
        probatio.Optional("comment", default=""): cv.string,
    }
)
DELETE_VACATION_SCHEMA = _schema({probatio.Required("vacation_id"): cv.positive_int})
ANSWER_VACATION_REQUEST_SCHEMA = _schema(
    {
        probatio.Required("child_id"): cv.positive_int,
        probatio.Required("request_id"): cv.positive_int,
        probatio.Required("days"): [
            {
                probatio.Required("date"): cv.date,
                probatio.Required("is_coming"): cv.boolean,
                probatio.Optional("entry_time"): cv.time,
                probatio.Optional("exit_time"): cv.time,
            }
        ],
        probatio.Optional("comment", default=""): cv.string,
    }
)
SEND_MESSAGE_SCHEMA = _schema(
    {
        probatio.Required("child_id"): cv.positive_int,
        probatio.Required("thread_id"): cv.positive_int,
        probatio.Required("text"): cv.string,
    }
)
PROBE_SCHEMA = _schema({probatio.Optional("reads", default=list): [cv.string]})


class _Actions(Protocol):
    """The part of Home Assistant's action registry this integration uses, fully typed."""

    def async_register(
        self,
        domain: str,
        service: str,
        service_func: Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]],
        schema: probatio.Schema,
        supports_response: SupportsResponse,
    ) -> None:
        """Register an action."""
        ...


async def _require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Refuse a call by a user who is no administrator.

    Raises:
        UnknownUser: The call names a user Home Assistant does not know.
        Unauthorized: The user is no administrator.
    """
    if call.context.user_id:
        user = await hass.auth.async_get_user(call.context.user_id)
        if user is None:
            raise UnknownUser(context=call.context, user_id=call.context.user_id)
        if not user.is_admin:
            raise Unauthorized(context=call.context)


def _raised(err: FamilyActionError) -> HomeAssistantError:
    """The error an action raises for one of the family's actions that could not be done."""
    kind = HomeAssistantError if isinstance(err, AulaError) else ServiceValidationError
    return kind(translation_domain=DOMAIN, translation_key=err.translation_key, translation_placeholders=err.placeholders)


def async_register_services(hass: HomeAssistant) -> None:
    """Register the actions: reading Aula and probing its widgets for administrators only, the rest for every user."""

    async def read(call: ServiceCall) -> ServiceResponse:
        await _require_admin(hass, call)
        entry: AulaConfigEntry | None = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")
        method: str = call.data["method"]
        try:
            answer: dict[str, Any] = await entry.runtime_data.client.read(
                method,
                dict(call.data["params"]),
                await pseudonym_key(hass),
                call.data["reveal"],
                call.data.get("body"),
            )
        except MethodRefusedError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="method_not_read_only",
                translation_placeholders={"method": method, "reason": str(err)},
            ) from None
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": error_text(err)},
            ) from None
        return answer

    def family(
        work: Callable[[ServiceCall, AulaConfigEntry], Coroutine[Any, Any, ServiceResponse]],
    ) -> Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]]:
        async def handle(call: ServiceCall) -> ServiceResponse:
            try:
                return await work(call, family_actions.loaded_entry(hass, call.data["config_entry_id"]))
            except FamilyActionError as err:
                raise _raised(err) from None

        return handle

    async def report_sick(call: ServiceCall, entry: AulaConfigEntry) -> ServiceResponse:
        child = family_actions.entry_child(entry, call.data["child_id"])
        return {"route": await family_actions.report_sick(hass, entry, child)}

    async def check_sick_report(call: ServiceCall, entry: AulaConfigEntry) -> ServiceResponse:
        child = family_actions.entry_child(entry, call.data["child_id"])
        return await family_actions.check_sick_report(hass, entry, child)

    async def undo_sick(call: ServiceCall, entry: AulaConfigEntry) -> ServiceResponse:
        child = family_actions.entry_child(entry, call.data["child_id"])
        return {"route": await family_actions.undo_sick(hass, entry, child)}

    async def add_vacation(call: ServiceCall, entry: AulaConfigEntry) -> ServiceResponse:
        children = [family_actions.entry_child(entry, child_id) for child_id in call.data["child_ids"]]
        await family_actions.add_vacation(hass, entry, children, call.data["start"], call.data["end"], call.data["comment"])
        return None

    async def delete_vacation(call: ServiceCall, entry: AulaConfigEntry) -> ServiceResponse:
        await family_actions.delete_vacation(hass, entry, call.data["vacation_id"])
        return None

    async def answer_vacation_request(call: ServiceCall, entry: AulaConfigEntry) -> ServiceResponse:
        child = family_actions.entry_child(entry, call.data["child_id"])
        days = vacation_days(call.data["days"])
        await family_actions.answer_vacation_request(hass, entry, child, call.data["request_id"], days, call.data["comment"])
        return None

    async def send_message(call: ServiceCall, entry: AulaConfigEntry) -> ServiceResponse:
        child = family_actions.entry_child(entry, call.data["child_id"])
        await family_actions.reply(hass, entry, child, call.data["thread_id"], call.data["text"])
        return None

    async def probe_widgets(call: ServiceCall, entry: AulaConfigEntry) -> ServiceResponse:
        return await family_actions.probe_widgets(hass, entry, call.data["reads"])

    async def probe_widgets_as_admin(call: ServiceCall) -> ServiceResponse:
        await _require_admin(hass, call)
        return await family(probe_widgets)(call)

    actions = _actions(hass)
    actions.async_register(DOMAIN, SERVICE_READ, read, READ_SCHEMA, SupportsResponse.ONLY)
    actions.async_register(DOMAIN, SERVICE_REPORT_SICK, family(report_sick), CHILD_SCHEMA, SupportsResponse.OPTIONAL)
    actions.async_register(DOMAIN, SERVICE_UNDO_SICK, family(undo_sick), CHILD_SCHEMA, SupportsResponse.OPTIONAL)
    actions.async_register(DOMAIN, SERVICE_CHECK_SICK_REPORT, family(check_sick_report), CHILD_SCHEMA, SupportsResponse.ONLY)
    actions.async_register(DOMAIN, SERVICE_ADD_VACATION, family(add_vacation), ADD_VACATION_SCHEMA, SupportsResponse.NONE)
    actions.async_register(DOMAIN, SERVICE_DELETE_VACATION, family(delete_vacation), DELETE_VACATION_SCHEMA, SupportsResponse.NONE)
    actions.async_register(
        DOMAIN, SERVICE_ANSWER_VACATION_REQUEST, family(answer_vacation_request), ANSWER_VACATION_REQUEST_SCHEMA, SupportsResponse.NONE
    )
    actions.async_register(DOMAIN, SERVICE_SEND_MESSAGE, family(send_message), SEND_MESSAGE_SCHEMA, SupportsResponse.NONE)
    actions.async_register(DOMAIN, SERVICE_PROBE_WIDGETS, probe_widgets_as_admin, PROBE_SCHEMA, SupportsResponse.ONLY)


def _actions(hass: HomeAssistant) -> _Actions:
    return hass.services
