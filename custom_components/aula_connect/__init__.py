"""Aula integration for Home Assistant."""

from collections.abc import Callable

import aiohttp
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType
import logging

from .aula_data import AulaConfigEntry, AulaRuntimeData
from .aula_data_coordinator import AulaDataCoordinator
from .aula_calendar_coordinator import AulaCalendarCoordinator
from aula_connect.client import AulaClient
from aula_connect.login import AulaLoginClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_MITID_USERNAME,
    CONF_ENTITY_PREFIX,
    DOMAIN,
    account_id,
)
from .websocket_api import async_register as async_register_websocket_api

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
]
# Home Assistant's annotation of it leaves a type unknown to a strict check, a bare `dict`; it is
# taken by name, with the type it has.
_config_entry_only_config_schema: Callable[[str], Callable[[ConfigType], ConfigType]] = getattr(
    cv, "config_entry_only_config_schema"
)
CONFIG_SCHEMA = _config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's websocket commands."""
    async_register_websocket_api(hass)
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
    data_coordinator = AulaDataCoordinator(entry.title, hass, client, entry)
    calendar_coordinator = AulaCalendarCoordinator(entry.title, hass, client, entry)

    await data_coordinator.async_config_entry_first_refresh()
    await calendar_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = AulaRuntimeData(client, data_coordinator, calendar_coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # No update listener: writing the renewed tokens to the entry would reload it each time.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AulaConfigEntry) -> bool:
    """Unload the entry's platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


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
