"""The config flow: an Aula login through MitID, set up, logged in again or reconfigured."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
import logging
from typing import Any, cast

from aula_connect.login.client import AulaLoginClient
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ID
from homeassistant.core import callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.http import current_request
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError, get_url
import aiohttp
import probatio

from .auth_session import AuthSession, auth_sessions, mitid_status
from .const import (
    AUTH_METHOD_APP,
    CONF_ACCESS_TOKEN,
    CONF_AUTH_METHOD,
    CONF_ENTITY_PREFIX,
    CONF_MITID_USERNAME,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    DOMAIN,
    account_id,
)
from .views import AulaAuthSelectIdentityView, AulaAuthStatusView, AulaAuthView

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_ID, default="Aula"): cv.string,
        probatio.Required(CONF_MITID_USERNAME): cv.string,
    }
)

USERNAME_SCHEMA = probatio.Schema({probatio.Required(CONF_MITID_USERNAME): cv.string})

IDENTITY_TIMEOUT = 300
"""Seconds the login waits for the user to choose between several MitID identities."""

LOGIN_KEPT_FOR = 60
"""Seconds a completed login is kept for its page to see it complete."""


class AulaCustomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up an Aula login, logging in through MitID."""

    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._entry_id = "Aula"
        self._mitid_username = ""
        self._tokens: dict[str, Any] = {}
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the name and the MitID user ID of a new Aula login."""
        if user_input is not None:
            self._entry_id = str(user_input.get(CONF_ID, "Aula")).strip()
            self._mitid_username = str(user_input[CONF_MITID_USERNAME])

            if not self._entry_id:
                return self.async_show_form(
                    last_step=False, step_id="user", data_schema=USER_SCHEMA, errors={"base": "invalid_id"}
                )
            if any(
                entry.data.get(CONF_ENTITY_PREFIX) == self._entry_id
                for entry in self._async_current_entries(include_ignore=False)
            ):
                return self.async_show_form(
                    last_step=False, step_id="user", data_schema=USER_SCHEMA, errors={"base": "id_already_in_use"}
                )

            await self.async_set_unique_id(account_id(self._mitid_username))
            self._abort_if_unique_id_configured()
            return await self.async_step_authenticate()

        return self.async_show_form(last_step=False, step_id="user", data_schema=USER_SCHEMA)

    async def async_step_authenticate(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Log in through MitID on the login page, which shows the QR code to scan."""
        session = auth_sessions(self.hass).get(self.flow_id)
        if session is None or (user_input is not None and session.error):
            self._start_login()
            return self._external_login()
        if session.completed and session.tokens:
            self._tokens = session.tokens
            return self.async_external_step_done(next_step_id="complete")
        if session.error:
            return self.async_external_step_done(next_step_id="auth_error")
        return self._external_login()

    def _start_login(self) -> None:
        """Start a MitID login in the background, and the pages that show it."""
        self.hass.http.register_view(AulaAuthView(self.hass))
        self.hass.http.register_view(AulaAuthStatusView(self.hass))
        self.hass.http.register_view(AulaAuthSelectIdentityView(self.hass))

        # A session of the login's own, as its cookies are the login's.
        http_session = async_create_clientsession(self.hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar())
        client = AulaLoginClient(http_session, mitid_username=self._mitid_username, auth_method=AUTH_METHOD_APP)
        session = AuthSession(client=client, http_session=http_session)
        auth_sessions(self.hass)[self.flow_id] = session

        async def choose_identity(identities: list[str]) -> str:
            """Wait for the user to choose between several MitID identities on the login page."""
            session.available_identities = identities
            session.status_message = "Please select an identity"
            future: asyncio.Future[str] = self.hass.loop.create_future()
            session.identity_future = future
            async with asyncio.timeout(IDENTITY_TIMEOUT):
                return await future

        client.identity_selector = choose_identity
        session.task = self.hass.async_create_background_task(
            self._authenticate_async(session), f"{DOMAIN} MitID login {self.flow_id}"
        )

    def _external_login(self) -> ConfigFlowResult:
        """The step that opens the MitID login page, and links to it should the browser not."""
        path = f"/api/aula_connect/auth/{self.flow_id}"
        return self.async_external_step(
            step_id="authenticate",
            url=f"{self._base_url()}{path}",
            description_placeholders={"login_path": path},
        )

    def _base_url(self) -> str:
        """The address the browser reaches Home Assistant at.

        The frontend opens only an absolute http(s) URL, and Home Assistant's own URLs may be
        ones the browser cannot reach, such as a dev container's, so the request's comes first.
        """
        request = current_request.get()
        if request is not None:
            return f"{request.scheme}://{request.host}"
        try:
            return get_url(self.hass)
        except NoURLAvailableError:
            return ""

    async def async_step_complete(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the entry with the login's tokens, or update the one logged in again."""
        data: dict[str, Any] = {
            CONF_MITID_USERNAME: self._mitid_username,
            CONF_AUTH_METHOD: AUTH_METHOD_APP,
            CONF_ACCESS_TOKEN: self._tokens.get("access_token", ""),
            CONF_REFRESH_TOKEN: self._tokens.get("refresh_token", ""),
            CONF_TOKEN_EXPIRES_AT: self._tokens.get("expires_at", 0),
        }
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                unique_id=account_id(self._mitid_username),
                data={**self._reauth_entry.data, **data},
            )
        return self.async_create_entry(title=self._entry_id, data={**data, CONF_ENTITY_PREFIX: self._entry_id})

    async def async_step_auth_error(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Say the login failed, and try again when the user asks to."""
        if user_input is not None:
            return await self.async_step_authenticate(user_input)
        return self.async_show_form(last_step=False, step_id="auth_error", errors={"base": "auth_failed"})

    async def _authenticate_async(self, session: AuthSession) -> None:
        """Run the MitID login, then go on with the flow whether it succeeded or not."""
        _LOGGER.info("Starting MitID authentication")
        monitor = self.hass.async_create_background_task(
            self._monitor_client_status(session), f"{DOMAIN} MitID status {self.flow_id}"
        )
        try:
            result = await session.client.authenticate()
        except Exception as err:  # noqa: BLE001 - any failure of the login is shown to the user
            _LOGGER.error("MitID authentication error: %s", err)
            session.error = str(err)
        else:
            tokens: object = result.get("tokens")
            if result.get("success") and isinstance(tokens, dict) and tokens:
                session.tokens = cast(dict[str, Any], tokens)
                session.completed = True
                session.status_message = "Authentication successful!"
                _LOGGER.info("MitID authentication successful")
            else:
                session.error = str(result.get("error") or "The login gave no tokens")
                _LOGGER.error("MitID authentication failed: %s", session.error)
        finally:
            monitor.cancel()
            if session.http_session is not None:
                session.http_session.detach()
                session.http_session = None

        self.hass.async_create_task(self._continue_flow())

    async def _continue_flow(self) -> None:
        """Take the flow on from the login, unless the user has closed it meanwhile."""
        # Home Assistant declares the flow manager's user input as a bare dict.
        flows: Any = self.hass.config_entries.flow
        with suppress(UnknownFlow):
            await flows.async_configure(flow_id=self.flow_id)

    async def _monitor_client_status(self, session: AuthSession) -> None:
        """Pass what the MitID client is doing on to the login page, until cancelled."""
        while True:
            status = mitid_status(session.client)
            if status is not None and not session.available_identities:
                session.status_message = status
            await asyncio.sleep(1)

    def async_remove(self) -> None:
        """Forget the flow's login when the flow ends, a completed one after a minute.

        The login page asks for the login's status once a second, and is to see it complete.
        """
        session = auth_sessions(self.hass).get(self.flow_id)
        if session is None or not session.completed:
            self._cleanup_session(self.flow_id)
            return
        flow_id = self.flow_id

        @callback
        def forget(_: datetime) -> None:
            self._cleanup_session(flow_id)

        async_call_later(self.hass, LOGIN_KEPT_FOR, forget)

    def _cleanup_session(self, flow_id: str) -> None:
        """Stop and forget the login of the flow `flow_id`."""
        session = auth_sessions(self.hass).pop(flow_id, None)
        if session is not None:
            session.end()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Log an entry in again, whose login Aula no longer accepts."""
        self._reauth_entry = self._get_reauth_entry()
        self._mitid_username = str(entry_data.get(CONF_MITID_USERNAME, ""))
        if not self._mitid_username:
            return await self.async_step_reauth_username()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_username(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the MitID user ID of an entry that has none."""
        if user_input is not None:
            self._mitid_username = str(user_input[CONF_MITID_USERNAME]).strip()
            if self._mitid_username:
                return await self.async_step_reauth_confirm()
            return self.async_show_form(
                last_step=False,
                step_id="reauth_username",
                data_schema=USERNAME_SCHEMA,
                errors={"base": "invalid_auth"},
            )
        return self.async_show_form(last_step=False, step_id="reauth_username", data_schema=USERNAME_SCHEMA)

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask the user to log in with MitID again."""
        if user_input is not None:
            self._cleanup_session(self.flow_id)
            return await self.async_step_authenticate()
        return self.async_show_form(
            last_step=False,
            step_id="reauth_confirm",
            description_placeholders={"username": self._mitid_username},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the MitID user ID of an entry, and log in with it."""
        entry = self._get_reconfigure_entry()
        self._reauth_entry = entry
        errors: dict[str, str] = {}
        if user_input is not None:
            self._mitid_username = str(user_input[CONF_MITID_USERNAME]).strip()
            if not self._mitid_username:
                errors["base"] = "invalid_auth"
            elif any(
                other.unique_id == account_id(self._mitid_username)
                for other in self._async_current_entries(include_ignore=False)
                if other.entry_id != entry.entry_id
            ):
                errors["base"] = "account_in_use"
            else:
                return await self.async_step_authenticate()

        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_MITID_USERNAME, default=str(entry.data.get(CONF_MITID_USERNAME, ""))
                ): cv.string,
            }
        )
        return self.async_show_form(last_step=False, step_id="reconfigure", data_schema=schema, errors=errors)
