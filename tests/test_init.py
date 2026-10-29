"""Tests of setting up and unloading an entry."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.const import CONF_ACCESS_TOKEN, DOMAIN

DATA = {
    "mitid_username": "parent",
    "auth_method": "APP",
    CONF_ACCESS_TOKEN: "access",
    "refresh_token": "refresh",
    "token_expires_at": 1.0,
}


@pytest.fixture
def offline() -> Iterator[None]:
    """Setup without reaching Aula: no first refresh, no platforms."""
    with (
        patch(
            "custom_components.aula_connect.AulaDataCoordinator.async_config_entry_first_refresh",
            AsyncMock(),
        ),
        patch(
            "custom_components.aula_connect.AulaCalendarCoordinator.async_config_entry_first_refresh",
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
async def test_the_tokens_the_client_renews_are_kept_in_the_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="Aula", title="Aula", data=DATA, version=2)
    entry.add_to_hass(hass)
    with patch("custom_components.aula_connect.AulaClient") as client:
        assert await hass.config_entries.async_setup(entry.entry_id)
    client.call_args.kwargs["token_update_callback"]("renewed-access", "renewed-refresh", 3.0)
    assert entry.data["access_token"] == "renewed-access"
    assert entry.data["refresh_token"] == "renewed-refresh"
    assert entry.data["token_expires_at"] == 3.0
    assert entry.data["mitid_username"] == "parent"
