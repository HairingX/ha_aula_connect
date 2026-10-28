"""Tests of the MitID login page."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.views import AulaAuthView


async def test_the_login_page_shows_the_qr_code_and_no_app_button(
    hass: HomeAssistant, hass_client_no_auth: Callable[[], Awaitable[Any]]
) -> None:
    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(AulaAuthView(hass))
    hass.data[DOMAIN] = {"auth_sessions": {"flow": {}}}
    client = await hass_client_no_auth()
    response = await client.get("/api/aula_connect/auth/flow")
    assert response.status == 200
    page = await response.text()
    assert 'id="qr-container"' in page
    assert "appswitch.mitid.dk" not in page


async def test_the_login_page_says_it_is_utf_8(
    hass: HomeAssistant, hass_client_no_auth: Callable[[], Awaitable[Any]]
) -> None:
    """Without it a browser shows the Danish letters wrongly."""
    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(AulaAuthView(hass))
    hass.data[DOMAIN] = {"auth_sessions": {"flow": {}}}
    client = await hass_client_no_auth()
    response = await client.get("/api/aula_connect/auth/flow")
    assert response.charset == "utf-8"
    assert '<meta charset="utf-8">' in await response.text()
