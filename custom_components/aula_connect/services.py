"""The integration's actions: read one method of Aula's API, with who someone is replaced."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from aula_connect.api.aula_errors import MethodRefusedError
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv

from .aula_data import AulaConfigEntry
from .const import DOMAIN
from .diagnostics import pseudonym_key
from .redact import error_text

SERVICE_READ = "read"

READ_SCHEMA = probatio.Schema(
    {
        probatio.Required("config_entry_id"): cv.string,
        probatio.Required("method"): cv.string,
        probatio.Optional("params", default={}): {cv.string: cv.string},
        probatio.Optional("reveal", default=False): probatio.Any(cv.boolean, [cv.string]),
    }
)


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


def async_register_services(hass: HomeAssistant) -> None:
    """Register the action that reads one method of Aula's API, for administrators only."""

    async def read(call: ServiceCall) -> ServiceResponse:
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None:
                raise UnknownUser(context=call.context, user_id=call.context.user_id)
            if not user.is_admin:
                raise Unauthorized(context=call.context)
        entry: AulaConfigEntry | None = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")
        method: str = call.data["method"]
        try:
            answer: dict[str, Any] = await entry.runtime_data.client.read(
                method, dict(call.data["params"]), await pseudonym_key(hass), call.data["reveal"]
            )
        except MethodRefusedError:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="method_not_read_only",
                translation_placeholders={"method": method},
            ) from None
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": error_text(err)},
            ) from None
        return answer

    _actions(hass).async_register(DOMAIN, SERVICE_READ, read, READ_SCHEMA, SupportsResponse.ONLY)


def _actions(hass: HomeAssistant) -> _Actions:
    return hass.services
