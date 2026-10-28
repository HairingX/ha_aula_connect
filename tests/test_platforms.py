"""Tests of the entities an entry sets up."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.aula_calendar_coordinator import AulaCalendarCoordinatorData
from custom_components.aula_connect.aula_data_coordinator import AulaDataCoordinatorData
from custom_components.aula_connect.const import DOMAIN

from .test_init import DATA


@pytest.fixture
def aula_without_children() -> Iterator[None]:
    """An Aula account with no children, no messages and no notifications."""
    data = AulaDataCoordinatorData("Aula", 23, [], [], [], [], [])
    calendar = AulaCalendarCoordinatorData([], [], [], [], [])
    with (
        patch("custom_components.aula_connect.AulaDataCoordinator._connection_check"),
        patch("custom_components.aula_connect.AulaDataCoordinator._fetch_data", return_value=data),
        patch("custom_components.aula_connect.AulaCalendarCoordinator._connection_check"),
        patch("custom_components.aula_connect.AulaCalendarCoordinator._fetch_data", return_value=calendar),
    ):
        yield


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
