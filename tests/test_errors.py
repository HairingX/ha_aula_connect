"""Tests of what an entry does when Aula cannot be read or no longer accepts the login."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from aula_connect.api.aula_errors import AulaCredentialError
from aula_connect.api.models.module import AulaDailyOverview
from aula_connect.http import HttpResponse, SentRequest
from aula_connect.login import AulaLoginClient
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.aula_connect.aula_data_coordinator import AulaDataCoordinator
from custom_components.aula_connect.const import DOMAIN

from .fake_aula import SCHOOL, FakeAula
from .test_calendars import at, events, set_up
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
        "custom_components.aula_connect.AulaClient",
        return_value=MagicMock(connection_check=AsyncMock(side_effect=AulaCredentialError("refresh token rejected"))),
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
        "custom_components.aula_connect.AulaClient",
        return_value=MagicMock(connection_check=AsyncMock(side_effect=ConnectionError("no route to Aula"))),
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
    client = AsyncMock()
    client.login.return_value = MagicMock(profiles=[], api_version=23)
    client.get_message_threads.side_effect = [ConnectionError("down"), ConnectionError("down"), []]
    coordinator = AulaDataCoordinator("Aula", hass, client, entry)
    for _ in range(3):
        await coordinator.async_refresh()
    warnings = [r for r in caplog.records if r.getMessage().startswith("Aula could be read only in part")]
    recoveries = [r for r in caplog.records if r.getMessage() == "Aula can be read in full again"]
    assert len(warnings) == 1 and "message threads: down" in warnings[0].getMessage()
    assert len(recoveries) == 1


async def test_a_refresh_token_aula_calls_invalid_asks_to_log_in_again(hass: HomeAssistant) -> None:
    """Through the library: Aula's token endpoint answers 400 invalid_grant for the expired token."""
    response = HttpResponse(
        status_code=400,
        reason="Bad Request",
        url="https://login.aula.dk/simplesaml/module.php/oidc/token.php",
        headers=CIMultiDictProxy(CIMultiDict[str]()),
        content=json.dumps({"error": "invalid_grant"}).encode(),
        encoding="utf-8",
        request=SentRequest("POST", "https://login.aula.dk/simplesaml/module.php/oidc/token.php"),
    )
    login_client = AulaLoginClient(MagicMock(), mitid_username="parent")
    setattr(login_client.session, "post", AsyncMock(return_value=response))
    entry = _entry(hass)
    with patch("custom_components.aula_connect.AulaLoginClient", return_value=login_client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == ["reauth"]


@pytest.mark.parametrize("method", ["login", "get_daily_overviews", "get_message_threads", "get_notifications"])
async def test_a_read_whose_login_aula_no_longer_accepts_asks_to_log_in_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, method: str
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, method, side_effect=AulaCredentialError("expired")):
        freezer.move_to(at(28, 10, 6))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == ["reauth"]


@pytest.mark.parametrize(
    ("method", "part"),
    [
        ("get_daily_overviews", "daily overviews"),
        ("get_message_threads", "message threads"),
        ("get_notifications", "notifications"),
    ],
)
async def test_a_part_aula_cannot_read_keeps_what_the_entities_had(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    method: str,
    part: str,
) -> None:
    aula.daily_overviews = [AulaDailyOverview(id=1, institution_profile=SCHOOL, status=3)]
    await set_up(hass, freezer)
    with patch.object(aula, method, side_effect=ConnectionError("down")):
        for minutes in (6, 11):
            freezer.move_to(at(28, 10, minutes))
            async_fire_time_changed(hass)
            await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("sensor.aula_presence_alma_school")
    assert state is not None and state.state != "unavailable"
    assert f"Aula could be read only in part: {part}: down" in caplog.text
    assert caplog.text.count("Aula could be read only in part") == 1


async def test_entities_aula_cannot_read_three_times_running_become_unavailable(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, "get_notifications", side_effect=ConnectionError("down")):
        for minutes in (6, 11, 16):
            freezer.move_to(at(28, 10, minutes))
            async_fire_time_changed(hass)
            await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("binary_sensor.aula_unread_post")
    assert state is not None and state.state == "unavailable"


@pytest.mark.parametrize(
    ("method", "calendar"),
    [
        ("get_birthday_events", "calendar.aula_birthdays_alma_school"),
        ("get_calendar_events", "calendar.aula_events_alma_school"),
        ("get_weekly_plans", "calendar.aula_weekly_plans_alma_school"),
        ("get_easyiq_weekly_plans", "calendar.aula_easyiq_weekly_plans_alma_school"),
        ("get_newsletters", "calendar.aula_newsletter_alma_school"),
    ],
)
async def test_a_period_aula_cannot_read_shows_nothing_and_says_so(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    method: str,
    calendar: str,
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, method, side_effect=ConnectionError("down")):
        assert await events(hass, calendar, at(1, 0, month=3, year=2027), at(8, 0, month=3, year=2027)) == []
    assert "for the period: down" in caplog.text


@pytest.mark.parametrize(
    ("method", "calendar"),
    [
        ("get_birthday_events", "calendar.aula_birthdays_alma_school"),
        ("get_calendar_events", "calendar.aula_events_alma_school"),
        ("get_weekly_plans", "calendar.aula_weekly_plans_alma_school"),
        ("get_easyiq_weekly_plans", "calendar.aula_easyiq_weekly_plans_alma_school"),
        ("get_newsletters", "calendar.aula_newsletter_alma_school"),
    ],
)
async def test_a_period_whose_login_aula_no_longer_accepts_asks_to_log_in_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, method: str, calendar: str
) -> None:
    await set_up(hass, freezer)
    with (
        patch.object(aula, method, side_effect=AulaCredentialError("expired")),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await events(hass, calendar, at(1, 0, month=3, year=2027), at(8, 0, month=3, year=2027))
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == ["reauth"]
