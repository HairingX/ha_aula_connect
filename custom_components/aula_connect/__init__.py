"""Aula integration for Home Assistant."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
import logging
from collections.abc import Callable
from typing import cast

from .aula_data import AulaHassData, remove_hass_data, set_hass_data
from .migration import async_take_over, offer_take_over
from .aula_data_coordinator import AulaDataCoordinator
from .aula_calendar_coordinator import AulaCalendarCoordinator
from aula_connect.client import AulaClient
from aula_connect.login import AulaLoginClient
from .const import (
    DOMAIN,
    CONF_MIGRATED_FROM,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_MITID_USERNAME,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
]


# Home Assistant annotates this with a bare `dict`, which a strict check reads as unknown.
_config_entry_only_config_schema = cast(
    Callable[[str], Callable[[ConfigType], ConfigType]],
    getattr(cv, "config_entry_only_config_schema"),
)
CONFIG_SCHEMA = _config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Offer to take over each installation of the Aula integration under its previous domain."""
    offer_take_over(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up platform from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})

    if CONF_MIGRATED_FROM in entry.data:
        await async_take_over(hass, entry)


    # Guard: unique_id must be set for entity unique_id prefix
    if not entry.unique_id:
        _LOGGER.error("Config entry has no unique_id — cannot create entities safely")
        raise ConfigEntryAuthFailed("Config entry has no unique_id. Please remove and re-add the integration.")

    # Migrate entity unique_ids: add entry.unique_id prefix for multi-account support
    ent_reg = er.async_get(hass)
    prefix = f"{entry.unique_id}_"
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if not ent.unique_id.startswith(prefix):
            new_uid = f"{prefix}{ent.unique_id}"
            _LOGGER.info("Migrating entity unique_id: %s → %s", ent.unique_id, new_uid)
            ent_reg.async_update_entity(ent.entity_id, new_unique_id=new_uid)

    # Guard: if tokens are missing (e.g. v1→v2 migration before reauth), abort
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
    hass_data: AulaHassData = {
        "client": client,
        "data_coordinator": data_coordinator,
        "calendar_coordinator": calendar_coordinator,
    }

    # Fetch initial data so we have data when entities subscribe
    await data_coordinator.async_config_entry_first_refresh()

    set_hass_data(hass, entry, hass_data)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Note: No add_update_listener here — token persistence via async_update_entry
    # would trigger reload loops. Reauth/reconfigure handle config changes already.

    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries to new version."""
    if config_entry.version < 2:
        _LOGGER.info(
            "Migrating config entry from version %s to 2", config_entry.version
        )
        # V1 had UniLogin username/password — can't auto-migrate to MitID tokens.
        # V1 always set unique_id via async_set_unique_id(CONF_ID).
        # Bump version only — async_setup_entry will raise ConfigEntryAuthFailed
        # (no access_token in data) which triggers reauth automatically.
        hass.config_entries.async_update_entry(config_entry, version=2)
        return True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    success = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not success:
        return False
    from .aula_data import get_aula_client
    try:
        client = get_aula_client(hass, entry)
        await hass.async_add_executor_job(client.close)
    except Exception:
        _LOGGER.debug("Failed to close client sessions during unload", exc_info=True)
    remove_hass_data(hass, entry)
    return True
