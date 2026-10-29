"""Tests of an entry being unique per MitID account while its entities keep their unique ids."""

from __future__ import annotations

from datetime import timedelta
import hashlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.aula_connect import async_migrate_entry
from custom_components.aula_connect.auth_session import AuthSession
from custom_components.aula_connect.config_flow import LOGIN_KEPT_FOR, AulaCustomConfigFlow
from custom_components.aula_connect.const import DOMAIN

from .test_init import DATA

TOKENS = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_at": 2.0}


def account(username: str) -> str:
    return hashlib.sha256(username.encode()).hexdigest()


async def _logged_in(flow: AulaCustomConfigFlow, session: AuthSession) -> None:
    session.tokens = dict(TOKENS)
    session.completed = True
    manager: Any = flow.hass.config_entries.flow
    flow.hass.async_create_task(manager.async_configure(flow_id=flow.flow_id))


async def _finish_login(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    """Go on after the login until the flow ends, as the frontend does after each step."""
    flow: Any = hass.config_entries.flow
    result: dict[str, Any] = {}
    for _ in range(3):
        await hass.async_block_till_done()
        result = dict(await flow.async_configure(flow_id))
        if result["type"] in (FlowResultType.CREATE_ENTRY, FlowResultType.ABORT):
            break
    await hass.async_block_till_done()
    return result


@pytest.fixture
async def mitid_login(hass: HomeAssistant) -> Any:
    """A MitID login that succeeds at once."""
    with (
        patch("custom_components.aula_connect.config_flow.AulaLoginClient", MagicMock()),
        patch.object(AulaCustomConfigFlow, "_authenticate_async", _logged_in),
    ):
        yield
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=LOGIN_KEPT_FOR + 1))


@pytest.mark.usefixtures("aula_without_children")
async def test_an_entry_from_before_becomes_unique_per_account_and_keeps_its_entities(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", title="Aula", data=DATA, version=2, minor_version=1)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert entry.unique_id == account("parent")
    assert entry.data["entity_prefix"] == "Aula"
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert "Aula_unread_message" in {entity.unique_id for entity in entities}


async def test_two_entries_from_before_for_one_account_keep_the_second_as_it_was(hass: HomeAssistant) -> None:
    first = MockConfigEntry(domain=DOMAIN, unique_id="Aula", data=DATA, version=2, minor_version=1)
    second = MockConfigEntry(domain=DOMAIN, unique_id="Aula 2", data=DATA, version=2, minor_version=1)
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    for entry in (first, second):
        assert await async_migrate_entry(hass, entry)
    assert first.unique_id == account("parent")
    assert second.unique_id == "Aula 2"
    assert second.data["entity_prefix"] == "Aula 2"


async def test_an_entry_from_a_later_version_is_not_migrated(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", data=DATA, version=3)
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)


async def test_a_new_entry_is_unique_per_account_and_named_for_its_entities(
    hass: HomeAssistant, mitid_login: Any
) -> None:
    assert await async_setup_component(hass, "http", {})
    flow: Any = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": "user"})
    with patch("custom_components.aula_connect.async_setup_entry", return_value=True):
        await flow.async_configure(result["flow_id"], {"id": "Family", "mitid_username": "parent"})
        assert (await _finish_login(hass, result["flow_id"]))["type"] is FlowResultType.CREATE_ENTRY
    entries = hass.config_entries.async_entries(DOMAIN)
    assert [(entry.title, entry.unique_id, entry.data["entity_prefix"]) for entry in entries] == [
        ("Family", account("parent"), "Family")
    ]


async def test_a_mitid_account_already_set_up_is_refused(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id=account("parent"), data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    ).add_to_hass(hass)
    flow: Any = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": "user"})
    aborted = await flow.async_configure(result["flow_id"], {"id": "Other", "mitid_username": "parent"})
    assert aborted["type"] is FlowResultType.ABORT
    assert aborted["reason"] == "already_configured"


async def test_a_name_already_used_is_refused(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id=account("other"), data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    ).add_to_hass(hass)
    flow: Any = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": "user"})
    form = await flow.async_configure(result["flow_id"], {"id": "Aula", "mitid_username": "parent"})
    assert form["errors"] == {"base": "id_already_in_use"}


async def test_reconfiguring_to_an_account_set_up_elsewhere_is_refused(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=account("parent"), data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN, unique_id=account("other"), data={**DATA, "entity_prefix": "B"}, version=2, minor_version=2
    ).add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    flow: Any = hass.config_entries.flow
    form = await flow.async_configure(result["flow_id"], {"mitid_username": "other"})
    assert form["errors"] == {"base": "account_in_use"}


async def test_logging_in_again_keeps_the_name_and_takes_the_new_tokens(
    hass: HomeAssistant, mitid_login: Any
) -> None:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=account("parent"), data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    flow: Any = hass.config_entries.flow
    with patch("custom_components.aula_connect.async_setup_entry", return_value=True):
        await flow.async_configure(result["flow_id"], {"mitid_username": "parent"})
        assert (await _finish_login(hass, result["flow_id"]))["reason"] == "reconfigure_successful"
    assert entry.data["entity_prefix"] == "Aula"
    assert entry.data["access_token"] == "new-access"
    assert entry.unique_id == account("parent")
