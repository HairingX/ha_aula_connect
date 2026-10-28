"""Take over an installation of the Aula integration under the domain `aula`."""

from __future__ import annotations

import logging

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_MIGRATED_FROM, DOMAIN

_LOGGER = logging.getLogger(__name__)

PREVIOUS_DOMAIN = "aula"
"""The domain the Aula integration was installed under before this one."""


def entries_to_take_over(hass: HomeAssistant) -> list[ConfigEntry]:
    """The installations under the previous domain that no entry of this one has taken over."""
    taken = {entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)}
    return [
        entry
        for entry in hass.config_entries.async_entries(PREVIOUS_DOMAIN)
        if entry.unique_id is not None and entry.unique_id not in taken
    ]


def offer_take_over(hass: HomeAssistant) -> None:
    """Offer each installation under the previous domain as a discovered one, to confirm."""
    for entry in entries_to_take_over(hass):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data={CONF_MIGRATED_FROM: entry.entry_id},
            ),
            eager_start=True,
        )


async def async_take_over(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move the devices and entities of the installation `entry` replaces onto `entry`.

    Each entity keeps its entity id, and with it its history, dashboards and automations; the
    installation it came from is then removed.
    """
    previous_id = entry.data.get(CONF_MIGRATED_FROM)
    previous = hass.config_entries.async_get_entry(previous_id) if previous_id else None
    if previous is not None:
        # The entity registry moves only entities no platform has loaded.
        if previous.state.recoverable:
            await hass.config_entries.async_unload(previous.entry_id)
        # Entities first: a device that leaves a config entry takes that entry's entities on it
        # out of the registry.
        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(entity_registry, previous.entry_id):
            entity_registry.async_update_entity_platform(
                entity.entity_id, DOMAIN, new_config_entry_id=entry.entry_id
            )
        device_registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(device_registry, previous.entry_id):
            device_registry.async_update_device(
                device.id,
                new_identifiers={
                    (DOMAIN if domain == PREVIOUS_DOMAIN else domain, identifier)
                    for domain, identifier in device.identifiers
                },
                new_config_entry_id=entry.entry_id,
            )
        await hass.config_entries.async_remove(previous.entry_id)
        _LOGGER.info("Took over the Aula installation %s", previous.title)
    data = {key: value for key, value in entry.data.items() if key != CONF_MIGRATED_FROM}
    hass.config_entries.async_update_entry(entry, data=data)
