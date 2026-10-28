"""Aula integration for Home Assistant."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
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

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
]


async def async_setup_entry(hass: HomeAssistant, entry: AulaConfigEntry) -> bool:
    """Set up platform from a ConfigEntry."""

    # Guard: if tokens are missing, ask to log in again
    if CONF_ACCESS_TOKEN not in entry.data or not entry.data[CONF_ACCESS_TOKEN]:
        raise ConfigEntryAuthFailed(
            "No access token found. Please re-authenticate with MitID."
        )

    access_token = entry.data[CONF_ACCESS_TOKEN]
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN, "")
    expires_at = entry.data.get(CONF_TOKEN_EXPIRES_AT, 0)
    mitid_username = entry.data.get(CONF_MITID_USERNAME, "")

    # Reusable login client instance — avoids session leak per refresh
    login_client = AulaLoginClient(mitid_username=mitid_username)

    def _persist_tokens(new_access: str, new_refresh: str, new_expires: float) -> None:
        """Persist refreshed tokens to config entry. Must be called from event loop."""
        try:
            new_data = {
                **entry.data,
                CONF_ACCESS_TOKEN: new_access,
                CONF_REFRESH_TOKEN: new_refresh,
                CONF_TOKEN_EXPIRES_AT: new_expires,
            }
            hass.config_entries.async_update_entry(entry, data=new_data)
        except Exception:
            _LOGGER.warning("Failed to persist refreshed tokens to config entry", exc_info=True)

    def token_update_callback(
        new_access: str, new_refresh: str, new_expires: float
    ) -> None:
        """Thread-safe callback — schedules token persistence on the event loop."""
        try:
            hass.loop.call_soon_threadsafe(
                _persist_tokens, new_access, new_refresh, new_expires
            )
        except RuntimeError:
            _LOGGER.warning(
                "Failed to persist refreshed tokens — event loop may be closed. "
                "Tokens are valid in memory but will be lost on restart."
            )

    client = AulaClient(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        mitid_username=mitid_username,
        login_client=login_client,
        token_update_callback=token_update_callback,
    )
    data_coordinator = AulaDataCoordinator(entry.title, hass, client, entry)
    calendar_coordinator = AulaCalendarCoordinator(entry.title, hass, client, entry)

    # Fetch initial data so we have data when entities subscribe
    await data_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = AulaRuntimeData(client, data_coordinator, calendar_coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Note: No add_update_listener here — token persistence via async_update_entry
    # would trigger reload loops. Reauth/reconfigure handle config changes already.

    return True


async def async_unload_entry(hass: HomeAssistant, entry: AulaConfigEntry) -> bool:
    """Unload a config entry."""
    success = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not success:
        return False
    try:
        await hass.async_add_executor_job(entry.runtime_data.client.close)
    except Exception:
        _LOGGER.debug("Failed to close client sessions during unload", exc_info=True)
    return True


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
