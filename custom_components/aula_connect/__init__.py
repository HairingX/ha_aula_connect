"""Aula integration for Home Assistant."""

from collections.abc import Callable

import aiohttp
from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType
import logging

from aula_connect import AnomalyKind
from aula_connect.client import AulaClient
from aula_connect.login import AulaLoginClient

from .aula_data import AulaConfigEntry, AulaRuntimeData
from .aula_data_coordinator import AulaDataCoordinator
from .aula_calendar_coordinator import AulaCalendarCoordinator
from .devices import listed_device
from .entity import device_info
from .family import FamilyMemory
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_MITID_USERNAME,
    CONF_ENTITY_PREFIX,
    DOMAIN,
    account_id,
    anomaly_issue_id,
)
from .services import async_register_services
from .websocket_api import async_register as async_register_websocket_api

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.TODO,
]
# Home Assistant's annotation of it leaves a type unknown to a strict check, a bare `dict`; it is
# taken by name, with the type it has.
_config_entry_only_config_schema: Callable[[str], Callable[[ConfigType], ConfigType]] = getattr(
    cv, "config_entry_only_config_schema"
)
CONFIG_SCHEMA = _config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's websocket commands and actions."""
    async_register_websocket_api(hass)
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AulaConfigEntry) -> bool:
    """Set up the entry's Aula client, its coordinators and its platforms.

    Raises:
        ConfigEntryAuthFailed: The entry has no access token; logging in again is asked for.
    """
    if CONF_ACCESS_TOKEN not in entry.data or not entry.data[CONF_ACCESS_TOKEN]:
        raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="login_expired")

    access_token = entry.data[CONF_ACCESS_TOKEN]
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN, "")
    expires_at = entry.data.get(CONF_TOKEN_EXPIRES_AT, 0)
    mitid_username = entry.data.get(CONF_MITID_USERNAME, "")

    # A session of the entry's own: Aula's cookies are the login's, not to be shared.
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    login_client = AulaLoginClient(session, mitid_username=mitid_username)

    def token_update_callback(new_access: str, new_refresh: str, new_expires: float) -> None:
        """Keep the renewed tokens in the entry, so a restart does not need a new login."""
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: new_access,
                CONF_REFRESH_TOKEN: new_refresh,
                CONF_TOKEN_EXPIRES_AT: new_expires,
            },
        )

    client = AulaClient(
        session,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        mitid_username=mitid_username,
        login_client=login_client,
        token_update_callback=token_update_callback,
    )
    memory = FamilyMemory(hass, entry.entry_id)
    await memory.async_load()
    data_coordinator = AulaDataCoordinator(entry.title, hass, client, entry, memory)
    calendar_coordinator = AulaCalendarCoordinator(entry.title, hass, client, entry)

    await data_coordinator.async_config_entry_first_refresh()
    await calendar_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = AulaRuntimeData(client, data_coordinator, calendar_coordinator, memory)
    # The children's devices are parts of the login's, which must be there first.
    dr.async_get(hass).async_get_or_create_from_info(config_entry_id=entry.entry_id, device_info=device_info(data_coordinator))

    @callback
    def device_switched(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        """Read Aula again when one of the entry's devices is disabled or enabled, so that its child is left out or back at once."""
        device = dr.async_get(hass).async_get(event.data["device_id"])
        if device is not None and device.config_entry_id == entry.entry_id:
            entry.async_create_task(hass, data_coordinator.async_request_refresh())

    entry.async_on_unload(
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, device_switched, event_filter=_disabled_by_changed)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # No update listener: writing the renewed tokens to the entry would reload it each time.
    return True


@callback
def _disabled_by_changed(data: dr.EventDeviceRegistryUpdatedData) -> bool:
    return data["action"] == "update" and "disabled_by" in data["changes"]


async def async_remove_config_entry_device(hass: HomeAssistant, entry: AulaConfigEntry, device: dr.AnyDeviceEntry) -> bool:
    """Let a child's device be deleted once Aula no longer lists the child for the login.

    The login's own device, and a child's while Aula lists it, would come back; they are kept.
    """
    if entry.state is not ConfigEntryState.LOADED:
        return False
    coordinator = entry.runtime_data.data_coordinator
    return not listed_device(device, coordinator.device_id, coordinator.data.listed_children)


async def async_unload_entry(hass: HomeAssistant, entry: AulaConfigEntry) -> bool:
    """Unload the entry's platforms, and write what it remembers of the family."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.memory.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: AulaConfigEntry) -> None:
    """Remove the entry's repair issues, and forget what it remembered of the family."""
    for kind in AnomalyKind:
        ir.async_delete_issue(hass, DOMAIN, anomaly_issue_id(kind, entry.entry_id))
    await FamilyMemory(hass, entry.entry_id).async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: AulaConfigEntry) -> bool:
    """Make the entry unique per MitID account, keeping its entities' unique ids."""
    if entry.version != 2:
        return False
    if entry.minor_version < 2:
        data = {**entry.data, CONF_ENTITY_PREFIX: entry.unique_id}
        unique_id = entry.unique_id
        username = entry.data.get(CONF_MITID_USERNAME, "")
        if username:
            candidate = account_id(username)
            taken = any(
                other.unique_id == candidate
                for other in hass.config_entries.async_entries(DOMAIN)
                if other.entry_id != entry.entry_id
            )
            if not taken:
                unique_id = candidate
        hass.config_entries.async_update_entry(entry, data=data, unique_id=unique_id, minor_version=2)
    return True
