"""Tests of the config flow around the MitID login: its outcomes, logging in again, and cleanup."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.aula_connect.auth_session import auth_session, mitid_status
from custom_components.aula_connect.config_flow import LOGIN_KEPT_FOR
from custom_components.aula_connect.const import DOMAIN

from .test_init import DATA
from .test_unique_entry import account

TOKENS = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_at": 2.0}
PREFIX = "custom_components.aula_connect"


def succeed(_: FakeLogin) -> dict[str, Any]:
    return {"success": True, "tokens": TOKENS}


def refuse(_: FakeLogin) -> dict[str, Any]:
    return {"success": False, "error": "Login refused"}


def give_no_tokens(_: FakeLogin) -> dict[str, Any]:
    return {"success": True, "tokens": None}


def crash(_: FakeLogin) -> dict[str, Any]:
    raise RuntimeError("MitID is down")


Outcome = Callable[["FakeLogin"], dict[str, Any] | Awaitable[dict[str, Any]]]


class FakeLogin:
    """A MitID login client whose login ends as the test says."""

    outcome: ClassVar[Outcome] = succeed

    def __init__(self, *_: Any, **__: Any) -> None:
        self.identity_selector: Callable[[list[str]], Awaitable[str]] | None = None
        self.chosen: str | None = None

    async def authenticate(self) -> dict[str, Any]:
        result = type(self).outcome(self)
        return await result if inspect.isawaitable(result) else result

    def get_mitid_client(self) -> Any:
        return SimpleNamespace(status_message="Login request has been made, open your MitID app now")


@pytest.fixture
async def login(hass: HomeAssistant) -> Any:
    """The fake login in place of the real one, and no setup of the entry it creates."""
    with (
        patch(f"{PREFIX}.config_flow.AulaLoginClient", FakeLogin),
        patch(f"{PREFIX}.async_setup_entry", return_value=True),
    ):
        yield FakeLogin
    FakeLogin.outcome = succeed
    a_minute_later(hass)


def a_minute_later(hass: HomeAssistant) -> None:
    """Move Home Assistant past the minute a completed login is kept for."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=LOGIN_KEPT_FOR + 1))


async def run_flow(hass: HomeAssistant, flow_id: str, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
    """Go on with the flow, as the frontend does, until it shows a form or ends."""
    flow: Any = hass.config_entries.flow
    result = dict(await flow.async_configure(flow_id, user_input))
    for _ in range(5):
        if result["type"] not in (FlowResultType.EXTERNAL_STEP, FlowResultType.EXTERNAL_STEP_DONE):
            break
        await hass.async_block_till_done()
        result = dict(await flow.async_configure(flow_id))
    await hass.async_block_till_done()
    return result


async def _start(hass: HomeAssistant) -> str:
    assert await async_setup_component(hass, "http", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return str(result.get("flow_id"))


async def test_a_login_that_succeeds_creates_the_entry(hass: HomeAssistant, login: Any) -> None:
    flow_id = await _start(hass)
    result = await run_flow(hass, flow_id, {"id": "Aula", "mitid_username": "parent"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["access_token"] == "new-access"
    assert result["data"]["entity_prefix"] == "Aula"


async def test_a_completed_login_is_kept_a_minute_for_its_page_to_see(hass: HomeAssistant, login: Any) -> None:
    flow_id = await _start(hass)
    await run_flow(hass, flow_id, {"id": "Aula", "mitid_username": "parent"})
    session = auth_session(hass, flow_id)
    assert session is not None
    assert session.completed
    a_minute_later(hass)
    await hass.async_block_till_done()
    assert auth_session(hass, flow_id) is None


@pytest.mark.parametrize("outcome", [refuse, give_no_tokens, crash])
async def test_a_login_that_fails_says_so_and_can_be_tried_again(
    hass: HomeAssistant, login: Any, outcome: Callable[[FakeLogin], dict[str, Any]]
) -> None:
    login.outcome = outcome
    flow_id = await _start(hass)
    result = await run_flow(hass, flow_id, {"id": "Aula", "mitid_username": "parent"})
    assert result["step_id"] == "auth_error"
    assert result["errors"] == {"base": "auth_failed"}
    assert not [task for task in asyncio.all_tasks() if "MitID status" in task.get_name()]

    login.outcome = succeed
    flow: Any = hass.config_entries.flow
    retry = await flow.async_configure(flow_id, {})
    assert retry["type"] is FlowResultType.EXTERNAL_STEP
    assert (await run_flow(hass, flow_id))["type"] is FlowResultType.CREATE_ENTRY


async def test_the_login_page_learns_what_mitid_is_doing(hass: HomeAssistant, login: Any) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait(_: FakeLogin) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"success": True, "tokens": TOKENS}

    login.outcome = wait
    flow_id = await _start(hass)
    flow: Any = hass.config_entries.flow
    await flow.async_configure(flow_id, {"id": "Aula", "mitid_username": "parent"})
    await started.wait()
    await asyncio.sleep(0)
    session = auth_session(hass, flow_id)
    assert session is not None
    assert session.status_message == "Login request has been made, open your MitID app now"
    release.set()
    assert (await run_flow(hass, flow_id))["type"] is FlowResultType.CREATE_ENTRY


async def test_the_user_chooses_between_several_identities(hass: HomeAssistant, login: Any) -> None:
    async def choose(client: FakeLogin) -> dict[str, Any]:
        assert client.identity_selector is not None
        client.chosen = await client.identity_selector(["Parent", "Teacher"])
        return {"success": True, "tokens": TOKENS}

    login.outcome = choose
    flow_id = await _start(hass)
    flow: Any = hass.config_entries.flow
    await flow.async_configure(flow_id, {"id": "Aula", "mitid_username": "parent"})
    session = auth_session(hass, flow_id)
    assert session is not None
    for _ in range(100):
        if session.identity_future is not None:
            break
        await asyncio.sleep(0.01)
    assert session.available_identities == ["Parent", "Teacher"]
    assert session.status_message == "Please select an identity"
    assert session.identity_future is not None
    session.identity_future.set_result("Teacher")
    assert (await run_flow(hass, flow_id))["type"] is FlowResultType.CREATE_ENTRY
    client: Any = session.client
    assert client.chosen == "Teacher"


async def test_a_blank_name_is_refused(hass: HomeAssistant) -> None:
    flow_id = await _start(hass)
    flow: Any = hass.config_entries.flow
    result = await flow.async_configure(flow_id, {"id": "  ", "mitid_username": "parent"})
    assert result["errors"] == {"base": "invalid_id"}


async def test_without_a_request_or_url_the_login_page_address_is_relative(hass: HomeAssistant, login: Any) -> None:
    with patch(f"{PREFIX}.config_flow.get_url", side_effect=NoURLAvailableError):
        flow_id = await _start(hass)
        flow: Any = hass.config_entries.flow
        result = await flow.async_configure(flow_id, {"id": "Aula", "mitid_username": "parent"})
    assert result["url"] == f"/api/aula_connect/auth/{flow_id}"


async def test_a_flow_closed_during_the_login_forgets_it_at_once(hass: HomeAssistant, login: Any) -> None:
    release = asyncio.Event()

    async def wait(_: FakeLogin) -> dict[str, Any]:
        await release.wait()
        return {"success": True, "tokens": TOKENS}

    login.outcome = wait
    flow_id = await _start(hass)
    flow: Any = hass.config_entries.flow
    await flow.async_configure(flow_id, {"id": "Aula", "mitid_username": "parent"})
    session = auth_session(hass, flow_id)
    assert session is not None and session.task is not None
    hass.config_entries.flow.async_abort(flow_id)
    assert auth_session(hass, flow_id) is None
    await hass.async_block_till_done()
    assert session.task.cancelled()
    assert session.http_session is None
    assert not hass.config_entries.async_entries(DOMAIN)


def _expired_entry(hass: HomeAssistant, username: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=account("parent"),
        data={**DATA, "mitid_username": username, "entity_prefix": "Aula"},
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    return entry


async def test_logging_in_again_asks_to_confirm_and_takes_the_new_tokens(hass: HomeAssistant, login: Any) -> None:
    assert await async_setup_component(hass, "http", {})
    entry = _expired_entry(hass, "parent")
    result: dict[str, Any] = dict(await entry.start_reauth_flow(hass))
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["username"] == "parent"
    done = await run_flow(hass, result["flow_id"], {})
    assert done["reason"] == "reauth_successful"
    assert entry.data["access_token"] == "new-access"
    assert entry.data["entity_prefix"] == "Aula"


async def test_logging_in_again_without_a_user_id_asks_for_one(hass: HomeAssistant, login: Any) -> None:
    assert await async_setup_component(hass, "http", {})
    entry = _expired_entry(hass, "")
    result: dict[str, Any] = dict(await entry.start_reauth_flow(hass))
    assert result["step_id"] == "reauth_username"
    flow: Any = hass.config_entries.flow
    blank = await flow.async_configure(result["flow_id"], {"mitid_username": " "})
    assert blank["errors"] == {"base": "invalid_auth"}
    confirm = await flow.async_configure(result["flow_id"], {"mitid_username": "parent"})
    assert confirm["step_id"] == "reauth_confirm"
    assert (await run_flow(hass, result["flow_id"], {}))["reason"] == "reauth_successful"
    assert entry.data["mitid_username"] == "parent"


async def test_reconfiguring_to_a_blank_user_id_is_refused(hass: HomeAssistant) -> None:
    entry = _expired_entry(hass, "parent")
    result = await entry.start_reconfigure_flow(hass)
    flow: Any = hass.config_entries.flow
    form = await flow.async_configure(result["flow_id"], {"mitid_username": " "})
    assert form["errors"] == {"base": "invalid_auth"}


def test_the_mitid_status_is_none_before_mitid_has_started() -> None:
    client: Any = MagicMock()
    client.get_mitid_client.return_value = None
    assert mitid_status(client) is None
    client.get_mitid_client.return_value = SimpleNamespace(status_message="Open your MitID app now...")
    assert mitid_status(client) == "Open your MitID app now..."
