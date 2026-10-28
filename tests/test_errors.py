"""Tests of what an entry does when Aula cannot be read or no longer accepts the login."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aula_connect.api.aula_errors import AulaCredentialError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.aula_data_coordinator import AulaDataCoordinator
from custom_components.aula_connect.const import DOMAIN

from .test_init import DATA


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="account", title="Aula", data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    return entry


async def test_a_login_aula_no_longer_accepts_asks_to_log_in_again(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    with patch(
        "custom_components.aula_connect.AulaDataCoordinator._connection_check",
        side_effect=AulaCredentialError("refresh token rejected"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == ["reauth"]


async def test_aula_out_of_reach_at_start_is_tried_again_without_an_unexpected_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _entry(hass)
    with patch(
        "custom_components.aula_connect.AulaDataCoordinator._connection_check",
        side_effect=ConnectionError("no route to Aula"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "Unexpected error" not in caplog.text
    assert "no route to Aula" in caplog.text


async def test_an_entry_without_a_token_says_the_login_has_expired(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account",
        data={**DATA, "access_token": "", "entity_prefix": "Aula"},
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.reason == "The login to Aula has expired. Log in with MitID again"


async def test_a_partial_failure_is_logged_once_and_the_recovery_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _entry(hass)
    client = MagicMock()
    client.login.return_value = MagicMock(profiles=[], api_version=23)
    client.get_message_threads.side_effect = [ConnectionError("down"), ConnectionError("down"), []]
    coordinator = AulaDataCoordinator("Aula", hass, client, entry)
    for _ in range(3):
        await coordinator.async_refresh()
    warnings = [r for r in caplog.records if r.getMessage().startswith("Aula could be read only in part")]
    recoveries = [r for r in caplog.records if r.getMessage() == "Aula can be read in full again"]
    assert len(warnings) == 1 and "message threads: down" in warnings[0].getMessage()
    assert len(recoveries) == 1
