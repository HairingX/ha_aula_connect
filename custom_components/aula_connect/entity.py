"""The entities of an Aula login, kept up to date by its coordinators."""

from __future__ import annotations

from typing import Generic, TypeVar

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .aula_calendar_coordinator import AulaCalendarCoordinator, AulaCalendarCoordinatorData
from .aula_data_coordinator import AulaDataCoordinator, AulaDataCoordinatorData
from .const import CONF_ENTITY_PREFIX, DOMAIN, MANUFACTURER

CONTEXT_TYPE = TypeVar("CONTEXT_TYPE")


def device_info(coordinator: AulaDataCoordinator | AulaCalendarCoordinator) -> DeviceInfo:
    """The device an Aula login's entities belong to."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.device_id)},
        name=coordinator.device_id,
        manufacturer=MANUFACTURER,
        sw_version=f"{coordinator.aula_version}",
    )


def unique_id(coordinator: AulaDataCoordinator | AulaCalendarCoordinator, key: str) -> str:
    """The unique id of an entity of the login: the name the entry was set up under and the key."""
    entry = coordinator.config_entry
    assert entry is not None
    return f"{entry.data[CONF_ENTITY_PREFIX]}_{key}"


class AulaEntityBase(Entity, Generic[CONTEXT_TYPE]):
    """An entity that shows what the data coordinator read, for one context such as a child."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: AulaDataCoordinator, name: str, context: CONTEXT_TYPE) -> None:
        self.coordinator = coordinator
        self.coordinator_context = context
        self._attr_translation_key = name
        self._attr_unique_id = unique_id(coordinator, name)
        self._attr_device_info = device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        """Follow the coordinator's updates while the entity is in Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update, self.coordinator_context)
        )

    async def async_update(self) -> None:
        """Ask the coordinator to read Aula again."""
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Show what the coordinator read."""
        self._init_data()
        self.async_write_ha_state()

    def _init_data(self) -> None:
        self._set_values(self.coordinator.data, self.coordinator_context)
        self._attr_available = self.coordinator.last_update_success

    def _set_values(self, data: AulaDataCoordinatorData, context: CONTEXT_TYPE) -> None:
        """Set the entity's values from the data the coordinator read."""


class AulaCalendarEntityBase(Entity):
    """A calendar entity that shows what the calendar coordinator read."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: AulaCalendarCoordinator, name: str) -> None:
        self.coordinator = coordinator
        self._attr_translation_key = name
        self._attr_unique_id = unique_id(coordinator, name)
        self._attr_device_info = device_info(coordinator)
        self._attr_available = coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        """Follow the coordinator's updates while the entity is in Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))

    async def async_update(self) -> None:
        """Ask the coordinator to read Aula again."""
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Show what the coordinator read, if it changed anything for the entity."""
        changed = self._handle_data_updated(self.coordinator.data)
        available = self.coordinator.last_update_success
        if changed or available != self._attr_available:
            self._attr_available = available
            self.async_write_ha_state()

    def _handle_data_updated(self, data: AulaCalendarCoordinatorData) -> bool:
        """Handle the update and return True if data has changed for the entity."""
        return False
