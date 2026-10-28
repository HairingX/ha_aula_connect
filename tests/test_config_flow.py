"""Tests of the MitID login in the config flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.const import DOMAIN


async def _start_login(
    hass: HomeAssistant, login_client: MagicMock | None = None, approval: str = "app_elsewhere"
) -> dict[str, Any]:
    assert await async_setup_component(hass, "http", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow: Any = hass.config_entries.flow
    with (
        patch("custom_components.aula_connect.config_flow.AulaLoginClient", login_client or MagicMock()),
        patch(
            "custom_components.aula_connect.config_flow.AulaCustomConfigFlow._authenticate_async",
            AsyncMock(),
        ),
    ):
        menu = await flow.async_configure(result.get("flow_id"), {"id": "Aula", "mitid_username": "parent"})
        step = await flow.async_configure(menu.get("flow_id"), {"next_step_id": approval})
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


async def test_the_user_id_is_asked_for_first_and_then_how_to_approve(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    schema = result.get("data_schema")
    assert schema is not None
    assert [str(key) for key in schema.schema] == ["id", "mitid_username"]
    flow: Any = hass.config_entries.flow
    menu = await flow.async_configure(result.get("flow_id"), {"id": "Aula", "mitid_username": "parent"})
    assert menu.get("type") is FlowResultType.MENU
    assert menu.get("step_id") == "approval"
    assert list(menu.get("menu_options")) == ["app_elsewhere", "app_here", "code_display"]


async def test_a_code_display_asks_for_its_code_and_the_password(hass: HomeAssistant) -> None:
    step = await _start_login(hass, approval="code_display")
    assert step.get("type") is FlowResultType.FORM
    assert step.get("step_id") == "token_credentials"


async def test_the_mitid_app_here_opens_the_login_page(hass: HomeAssistant) -> None:
    step = await _start_login(hass, approval="app_here")
    assert step.get("type") is FlowResultType.EXTERNAL_STEP


async def test_the_mitid_app_logs_in_with_the_app_method(hass: HomeAssistant) -> None:
    """The stored method keeps the names it has had: APP and TOKEN."""
    login_client = MagicMock()
    await _start_login(hass, login_client)
    assert login_client.call_args.kwargs["auth_method"] == "APP"


async def test_the_login_page_learns_how_the_user_approves(hass: HomeAssistant) -> None:
    step = await _start_login(hass, approval="app_here")
    session = hass.data[DOMAIN]["auth_sessions"][step.get("flow_id")]
    assert session["approval"] == "app_here"


async def test_reconfiguring_asks_for_the_user_id_and_then_how_to_approve(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="Aula",
        data={"mitid_username": "parent", "auth_method": "APP", "mitid_approval": "app_here"},
        version=2,
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    schema = result.get("data_schema")
    assert schema is not None
    fields = {str(key): key for key in schema.schema}
    assert list(fields) == ["mitid_username"]
    assert fields["mitid_username"].default() == "parent"
    flow: Any = hass.config_entries.flow
    menu = await flow.async_configure(result.get("flow_id"), {"mitid_username": "parent"})
    assert menu.get("type") is FlowResultType.MENU
    assert list(menu.get("menu_options")) == ["app_elsewhere", "app_here", "code_display"]
