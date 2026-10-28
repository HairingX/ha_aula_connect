"""Tests of taking over an installation of the Aula integration under its previous domain."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import (
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.const import CONF_ACCESS_TOKEN, CONF_MIGRATED_FROM, DOMAIN
from custom_components.aula_connect.migration import (
    PREVIOUS_DOMAIN,
    async_take_over,
    entries_to_take_over,
)

DATA: dict[str, Any] = {
    "mitid_username": "parent",
    "auth_method": "APP",
    CONF_ACCESS_TOKEN: "access",
    "refresh_token": "refresh",
    "token_expires_at": 1.0,
}


async def _configure(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    # HA declares the flow manager's user input as a bare `dict`.
    flow: Any = hass.config_entries.flow
    result: ConfigFlowResult = await flow.async_configure(flow_id, {})
    return dict(result)


def previous_installation(hass: HomeAssistant, unique_id: str = "Aula") -> MockConfigEntry:
    """An installation under the previous domain, with a device and two entities."""
    entry = MockConfigEntry(domain=PREVIOUS_DOMAIN, unique_id=unique_id, title=unique_id, data=DATA, version=2)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(PREVIOUS_DOMAIN, unique_id)}, name=unique_id
    )
    registry = er.async_get(hass)
    for key, entity_id in (("status", "sensor.emma_status"), ("messages", "binary_sensor.unread_messages")):
        registry.async_get_or_create(
            entity_id.split(".")[0],
            PREVIOUS_DOMAIN,
            f"{unique_id}_{key}",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=entity_id.split(".")[1],
        )
    return entry


async def test_an_installation_under_the_previous_domain_is_offered(hass: HomeAssistant) -> None:
    previous_installation(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == [SOURCE_INTEGRATION_DISCOVERY]
    assert flows[0].get("step_id") == "take_over"


async def test_an_installation_already_taken_over_is_not_offered(hass: HomeAssistant) -> None:
    previous_installation(hass)
    MockConfigEntry(domain=DOMAIN, unique_id="Aula", data=DATA).add_to_hass(hass)
    assert entries_to_take_over(hass) == []


async def test_confirming_creates_an_entry_with_the_previous_data(hass: HomeAssistant) -> None:
    previous = previous_installation(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_INTEGRATION_DISCOVERY}, data={CONF_MIGRATED_FROM: previous.entry_id}
    )
    assert result.get("type") is FlowResultType.FORM
    assert result.get("description_placeholders") == {"name": "Aula"}
    with patch("custom_components.aula_connect.async_setup_entry", AsyncMock(return_value=True)):
        created = await _configure(hass, str(result.get("flow_id")))
    assert created.get("type") is FlowResultType.CREATE_ENTRY
    assert created.get("title") == "Aula"
    assert created.get("data") == {**DATA, CONF_MIGRATED_FROM: previous.entry_id}
    entry = created.get("result")
    assert isinstance(entry, ConfigEntry) and entry.unique_id == "Aula"


async def test_an_installation_removed_before_confirming_is_not_taken_over(hass: HomeAssistant) -> None:
    previous = previous_installation(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_INTEGRATION_DISCOVERY}, data={CONF_MIGRATED_FROM: previous.entry_id}
    )
    await hass.config_entries.async_remove(previous.entry_id)
    aborted = await _configure(hass, str(result.get("flow_id")))
    assert aborted.get("type") is FlowResultType.ABORT
    assert aborted.get("reason") == "previous_installation_gone"


async def test_a_vanished_installation_is_not_taken_over(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_INTEGRATION_DISCOVERY}, data={CONF_MIGRATED_FROM: "gone"}
    )
    assert result.get("type") is FlowResultType.ABORT
    assert result.get("reason") == "previous_installation_gone"


async def test_taking_over_keeps_every_entity_id_and_removes_the_previous_installation(
    hass: HomeAssistant,
) -> None:
    previous = previous_installation(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="Aula", title="Aula", data={**DATA, CONF_MIGRATED_FROM: previous.entry_id}
    )
    entry.add_to_hass(hass)

    await async_take_over(hass, entry)

    registry = er.async_get(hass)
    moved = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert sorted(entity.entity_id for entity in moved) == ["binary_sensor.unread_messages", "sensor.emma_status"]
    assert {entity.platform for entity in moved} == {DOMAIN}
    assert sorted(entity.unique_id for entity in moved) == ["Aula_messages", "Aula_status"]
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [device.identifiers for device in devices] == [{(DOMAIN, "Aula")}]
    assert {entity.device_id for entity in moved} == {devices[0].id}
    assert hass.config_entries.async_get_entry(previous.entry_id) is None
    assert CONF_MIGRATED_FROM not in entry.data
    assert entry.data[CONF_ACCESS_TOKEN] == "access"


async def test_a_loaded_previous_installation_is_unloaded_first(hass: HomeAssistant) -> None:
    previous = previous_installation(hass)
    previous.mock_state(hass, ConfigEntryState.LOADED)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", data={**DATA, CONF_MIGRATED_FROM: previous.entry_id})
    entry.add_to_hass(hass)
    unload = AsyncMock(return_value=True)
    with patch.object(hass.config_entries, "async_unload", unload):
        await async_take_over(hass, entry)
    assert unload.await_args_list[0].args == (previous.entry_id,)
