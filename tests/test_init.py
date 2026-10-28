"""Tests of setting up and unloading an entry."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.const import CONF_ACCESS_TOKEN, CONF_MIGRATED_FROM, DOMAIN

from .test_migration import DATA, previous_installation


@pytest.fixture
def offline() -> Iterator[None]:
    """Setup without reaching Aula: no first refresh, no platforms."""
    with (
        patch(
            "custom_components.aula_connect.AulaDataCoordinator.async_config_entry_first_refresh",
            AsyncMock(),
        ),
        patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", AsyncMock()),
        patch("homeassistant.config_entries.ConfigEntries.async_unload_platforms", AsyncMock(return_value=True)),
    ):
        yield


@pytest.mark.usefixtures("offline")
async def test_an_entry_sets_up_and_unloads(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", title="Aula", data=DATA, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.usefixtures("offline")
async def test_an_entry_without_a_token_asks_to_log_in_again(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", data={**DATA, CONF_ACCESS_TOKEN: ""}, version=2)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert [flow.get("context", {}).get("source") for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)] == ["reauth"]


@pytest.mark.usefixtures("offline")
async def test_setting_up_an_entry_that_takes_over_moves_the_previous_installation(hass: HomeAssistant) -> None:
    previous = previous_installation(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="Aula",
        title="Aula",
        data={**DATA, CONF_MIGRATED_FROM: previous.entry_id},
        version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    moved = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert sorted(entity.entity_id for entity in moved) == ["binary_sensor.unread_messages", "sensor.emma_status"]
    assert hass.config_entries.async_get_entry(previous.entry_id) is None
    assert CONF_MIGRATED_FROM not in entry.data
