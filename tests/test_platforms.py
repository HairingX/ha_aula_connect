"""Tests of the entities an entry sets up."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.aula_data import AulaRuntimeData
from custom_components.aula_connect.const import DOMAIN

from .test_init import DATA


@pytest.mark.usefixtures("aula_without_children")
async def test_an_account_without_children_has_only_its_unread_sensors(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", title="Aula", data=DATA, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert sorted(entity.unique_id for entity in entities) == [
        "Aula_unread_calendar",
        "Aula_unread_gallery",
        "Aula_unread_message",
        "Aula_unread_post",
        "Aula_unread_presence",
    ]
    for entity in entities:
        state = hass.states.get(entity.entity_id)
        assert state is not None and state.state == "off"
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [device.identifiers for device in devices] == [{(DOMAIN, "Aula")}]

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.usefixtures("aula_without_children")
async def test_the_entry_keeps_its_client_and_coordinators_as_runtime_data(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", title="Aula", data=DATA, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    assert isinstance(runtime, AulaRuntimeData)
    assert runtime.data_coordinator.config_entry is entry
    assert runtime.calendar_coordinator.config_entry is entry
    assert DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]

    with patch.object(runtime.client, "close") as close:
        assert await hass.config_entries.async_unload(entry.entry_id)
    close.assert_called_once_with()


@pytest.mark.usefixtures("aula_without_children")
async def test_the_entities_are_unavailable_while_aula_cannot_be_read(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", title="Aula", data=DATA, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.data_coordinator
    entity_id = "binary_sensor.aula_unread_message"

    coordinator.async_set_update_error(Exception("Aula did not answer"))
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None and state.state == "unavailable"

    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None and state.state == "off"


def test_the_platforms_update_their_entities_in_parallel() -> None:
    """The entities read what the coordinators fetched, so updating them never waits on Aula."""
    from custom_components.aula_connect import binary_sensor, calendar, sensor

    assert [module.PARALLEL_UPDATES for module in (sensor, binary_sensor, calendar)] == [0, 0, 0]
