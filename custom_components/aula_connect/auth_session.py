"""A MitID login in progress, shared by the config flow and the login page."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp
from aula_connect.login.client import AulaLoginClient
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

AUTH_SESSIONS: HassKey[dict[str, AuthSession]] = HassKey(f"{DOMAIN}_auth_sessions")
"""The logins in progress, by the id of the flow that started them."""


@dataclass
class AuthSession:
    """One MitID login: its client, how far it has come, and what it ended with."""

    client: AulaLoginClient
    status_message: str = "Open your MitID app now..."
    completed: bool = False
    error: str | None = None
    tokens: dict[str, Any] | None = None
    http_session: aiohttp.ClientSession | None = None
    identity_future: asyncio.Future[str] | None = None
    available_identities: list[str] | None = None
    task: asyncio.Task[None] | None = None


    def end(self) -> None:
        """Stop the login if it still runs, and let go of its HTTP session."""
        if self.task is not None and not self.task.done():
            self.task.cancel()
        if self.identity_future is not None and not self.identity_future.done():
            self.identity_future.cancel()
        if self.http_session is not None:
            self.http_session.detach()
            self.http_session = None


def auth_sessions(hass: HomeAssistant) -> dict[str, AuthSession]:
    """The logins in progress."""
    return hass.data.setdefault(AUTH_SESSIONS, {})


def auth_session(hass: HomeAssistant, flow_id: str) -> AuthSession | None:
    """The login the flow `flow_id` started, None if it has none."""
    return auth_sessions(hass).get(flow_id)


def mitid_status(client: AulaLoginClient) -> str | None:
    """What the MitID client says it is doing, None before it has started."""
    status: object = getattr(client.get_mitid_client(), "status_message", None)
    return status if isinstance(status, str) else None


def qr_codes_svg(client: AulaLoginClient) -> tuple[str, str] | None:
    """The two QR codes MitID asks to scan, as SVG, None while it asks for none."""
    return client.get_qr_codes_svg()
