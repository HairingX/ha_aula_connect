"""Tests of the MitID login in the config flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.aula_connect.const import DOMAIN


async def _start_login(hass: HomeAssistant) -> dict[str, Any]:
    assert await async_setup_component(hass, "http", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow: Any = hass.config_entries.flow
    with (
        patch("custom_components.aula_connect.config_flow.AulaLoginClient", MagicMock()),
        patch(
            "custom_components.aula_connect.config_flow.AulaCustomConfigFlow._authenticate_async",
            AsyncMock(),
        ),
    ):
        step = await flow.async_configure(
            result.get("flow_id"), {"id": "Aula", "mitid_username": "parent", "mitid_use_token": False}
        )
    return dict(step)


async def test_the_login_page_opens_at_an_absolute_url(hass: HomeAssistant) -> None:
    """The frontend opens an external step's URL only when it is absolute http(s)."""
    hass.config.internal_url = "http://192.0.2.10:8123"
    step = await _start_login(hass)
    path = f"/api/aula_connect/auth/{step.get('flow_id')}"
    assert step.get("type") is FlowResultType.EXTERNAL_STEP
    assert step.get("url") == f"http://192.0.2.10:8123{path}"
    assert step.get("description_placeholders") == {"login_path": path}


async def test_the_login_page_is_at_the_address_the_browser_used(hass: HomeAssistant) -> None:
    request = MagicMock(scheme="http", host="localhost:1337")
    with patch("custom_components.aula_connect.config_flow.current_request") as current:
        current.get.return_value = request
        step = await _start_login(hass)
    assert str(step.get("url")).startswith("http://localhost:1337/api/aula_connect/auth/")
