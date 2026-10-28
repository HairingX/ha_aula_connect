"""Tests of the MitID login page."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.views import AulaAuthView


async def test_the_login_page_opens_the_mitid_app(
    hass: HomeAssistant, hass_client_no_auth: Callable[[], Awaitable[Any]]
) -> None:
    """On the phone that holds the MitID app, a QR code on its own screen cannot be scanned."""
    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(AulaAuthView(hass))
    hass.data[DOMAIN] = {"auth_sessions": {"flow": {}}}
    client = await hass_client_no_auth()
    response = await client.get("/api/aula_connect/auth/flow")
    assert response.status == 200
    assert 'href="mitid://"' in await response.text()
