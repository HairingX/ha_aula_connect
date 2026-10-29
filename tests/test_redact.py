"""Tests that no token reaches a log or a message through an error's text."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from urllib3 import HTTPSConnectionPool
from urllib3.connection import HTTPSConnection
from urllib3.exceptions import MaxRetryError, NewConnectionError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.aula_calendar_coordinator import AulaCalendarCoordinator
from custom_components.aula_connect.aula_data_coordinator import AulaDataCoordinator
from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.redact import error_text

from .test_init import DATA

SECRET = "s3cr3t-t0ken"


def _unreachable() -> requests.ConnectionError:
    """The error the HTTP client raises when Aula is out of reach, with the URL in its text."""
    url = f"/api/v23?method=profiles.getProfilesByLogin&access_token={SECRET}"
    pool = HTTPSConnectionPool("www.aula.dk")
    reason = NewConnectionError(HTTPSConnection("www.aula.dk"), "Failed to establish a new connection: [Errno 111] Connection refused")
    return requests.ConnectionError(MaxRetryError(pool, url, reason))


def test_the_http_clients_error_holds_the_token_and_its_text_does_not() -> None:
    err = _unreachable()
    assert SECRET in str(err)
    assert SECRET not in error_text(err)
    assert "access_token=REDACTED" in error_text(err)


@pytest.mark.parametrize(
    "text",
    [
        f"refresh_token={SECRET}&grant_type=refresh_token",
        f'{{"access_token": "{SECRET}"}}',
        f"code={SECRET}",
        f"Password: '{SECRET}'",
    ],
)
def test_tokens_codes_and_passwords_are_taken_out(text: str) -> None:
    assert SECRET not in error_text(ValueError(text))


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="account", title="Aula", data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    return entry


async def test_aula_out_of_reach_at_start_logs_no_token(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry = _entry(hass)
    with patch(
        "custom_components.aula_connect.AulaClient",
        return_value=MagicMock(connection_check=AsyncMock(side_effect=_unreachable())),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "Max retries exceeded" in caplog.text
    assert SECRET not in caplog.text
    assert SECRET not in str(entry.reason)


async def test_a_partial_failure_logs_no_token(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry = _entry(hass)
    client = AsyncMock()
    client.login.return_value = MagicMock(profiles=[], api_version=23)
    client.get_message_threads.side_effect = _unreachable()
    coordinator = AulaDataCoordinator("Aula", hass, client, entry)
    await coordinator.async_refresh()
    assert "Aula could be read only in part" in caplog.text
    assert SECRET not in caplog.text


async def test_a_calendar_that_cannot_be_read_logs_no_token(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _entry(hass)
    client = AsyncMock()
    client.get_birthday_events.side_effect = _unreachable()
    coordinator = AulaCalendarCoordinator("Aula", hass, client, entry)
    assert await coordinator.get_birthdays_for_interval([], MagicMock(), MagicMock()) == []
    assert "Failed to fetch birthdays" in caplog.text
    assert SECRET not in caplog.text
