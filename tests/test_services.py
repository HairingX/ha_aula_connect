"""Tests of reading one method of Aula's API through the action, and of asking for the diagnostics
when Aula sends what the integration does not know."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from aula_connect import Anomaly, AnomalyKind
from aula_connect.api.aula_errors import MethodRefusedError, ParseError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized, UnknownUser
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.aula_connect.const import DOMAIN

from .fake_aula import FakeAula
from .test_calendars import set_up
from .test_init import DATA


async def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="account", data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _read(
    hass: HomeAssistant, entry_id: str, method: str = "profiles.getProfileContext", context: Context | None = None
) -> Any:
    return await hass.services.async_call(
        DOMAIN,
        "read",
        {"config_entry_id": entry_id, "method": method, "params": {"portalrole": "guardian"}},
        blocking=True,
        return_response=True,
        context=context,
    )


@pytest.mark.usefixtures("aula_without_children")
async def test_the_action_reads_a_method_and_answers_as_the_diagnostics_do(hass: HomeAssistant) -> None:
    entry = await _entry(hass)
    client: Any = entry.runtime_data.client

    assert await _read(hass, entry.entry_id) == {"endpoint": "profiles.getProfileContext"}
    method, params, key = client.read.call_args.args
    assert (method, params, len(key)) == ("profiles.getProfileContext", {"portalrole": "guardian"}, 32)


@pytest.mark.usefixtures("aula_without_children")
async def test_the_action_refuses_an_entry_not_loaded_and_a_method_that_does_not_only_get(hass: HomeAssistant) -> None:
    entry = await _entry(hass)
    client: Any = entry.runtime_data.client
    other = MockConfigEntry(domain="other", state=ConfigEntryState.LOADED)
    other.add_to_hass(hass)
    for entry_id in ("no-such-entry", other.entry_id):
        with pytest.raises(ServiceValidationError) as unknown:
            await _read(hass, entry_id)
        assert unknown.value.translation_key == "entry_not_loaded"
    client.read.side_effect = MethodRefusedError("not a get")
    with pytest.raises(ServiceValidationError) as refused:
        await _read(hass, entry.entry_id, "messaging.deleteThread")
    assert refused.value.translation_placeholders == {"method": "messaging.deleteThread"}
    assert refused.value.__cause__ is None and refused.value.__suppress_context__
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as unloaded:
        await _read(hass, entry.entry_id)
    assert unloaded.value.translation_key == "entry_not_loaded"


@pytest.mark.usefixtures("aula_without_children")
async def test_only_an_administrator_may_read_aula(
    hass: HomeAssistant, hass_admin_user: MockUser, hass_read_only_user: MockUser
) -> None:
    entry = await _entry(hass)
    assert await _read(hass, entry.entry_id, context=Context(user_id=hass_admin_user.id)) is not None
    with pytest.raises(Unauthorized) as refused:
        await _read(hass, entry.entry_id, context=Context(user_id=hass_read_only_user.id))
    assert not isinstance(refused.value, UnknownUser)
    with pytest.raises(UnknownUser):
        await _read(hass, entry.entry_id, context=Context(user_id="no-such-user"))


@pytest.mark.usefixtures("aula_without_children")
async def test_the_action_says_why_aula_could_not_be_read_without_its_token(hass: HomeAssistant) -> None:
    entry = await _entry(hass)
    client: Any = entry.runtime_data.client
    client.read.side_effect = ConnectionError("url: /api?access_token=s3cr3t")
    with pytest.raises(HomeAssistantError) as failed:
        await _read(hass, entry.entry_id)
    assert failed.value.translation_placeholders == {"error": "url: /api?access_token=REDACTED"}
    client.read.side_effect = ValueError("not a URL")
    with pytest.raises(HomeAssistantError) as invalid:
        await _read(hass, entry.entry_id)
    assert not isinstance(invalid.value, ServiceValidationError)


async def test_aula_sending_what_is_not_known_asks_for_the_diagnostics_a_kind_an_issue_until_it_stops(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aula: FakeAula
) -> None:
    await set_up(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    coordinator = entry.runtime_data.data_coordinator
    issues = ir.async_get(hass)
    unknown = f"unknown_notification_area_{entry.entry_id}"
    version = f"api_version_changed_{entry.entry_id}"
    seen = dt_util.utcnow()
    aula.found = [
        Anomaly(AnomalyKind.UNKNOWN_NOTIFICATION_AREA, "Homework", seen),
        Anomaly(AnomalyKind.UNKNOWN_NOTIFICATION_AREA, "Absence", seen),
        Anomaly(AnomalyKind.API_VERSION_CHANGED, "24", seen - timedelta(days=2)),
    ]
    await coordinator.async_refresh()
    issue = issues.async_get_issue(DOMAIN, unknown)
    assert issue is not None and issue.translation_key == "unknown_notification_area"
    assert issue.translation_placeholders == {"details": "Absence, Homework"}
    assert issues.async_get_issue(DOMAIN, version) is None

    freezer.tick(timedelta(days=1, seconds=1))
    await coordinator.async_refresh()
    assert issues.async_get_issue(DOMAIN, unknown) is None


async def _ignored_issue_after_a_reload(hass: HomeAssistant, freezer: FrozenDateTimeFactory, aula: FakeAula) -> str:
    await set_up(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    aula.found = [Anomaly(AnomalyKind.UNREADABLE_RESPONSE, "weekly plans", dt_util.utcnow())]
    await entry.runtime_data.data_coordinator.async_refresh()
    issue = f"unreadable_response_{entry.entry_id}"
    ir.async_ignore_issue(hass, DOMAIN, issue, True)
    aula.found = []
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.data_coordinator.async_refresh()
    return issue


async def test_an_ignored_issue_stays_ignored_through_a_reload_and_goes_with_the_entry(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aula: FakeAula
) -> None:
    issue = await _ignored_issue_after_a_reload(hass, freezer, aula)
    found = ir.async_get(hass).async_get_issue(DOMAIN, issue)
    assert found is not None and found.dismissed_version is not None
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert await hass.config_entries.async_remove(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None


async def test_an_issue_from_before_a_reload_goes_once_a_whole_window_passed_without_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aula: FakeAula
) -> None:
    issue = await _ignored_issue_after_a_reload(hass, freezer, aula)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    freezer.tick(timedelta(days=1))
    await entry.runtime_data.data_coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None


async def test_an_issue_names_ten_details_and_how_many_more(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aula: FakeAula
) -> None:
    await set_up(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    aula.found = [Anomaly(AnomalyKind.UNKNOWN_NOTIFICATION_AREA, f"Area{n:02}", dt_util.utcnow()) for n in range(12)]
    await entry.runtime_data.data_coordinator.async_refresh()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"unknown_notification_area_{entry.entry_id}")
    assert issue is not None and issue.translation_placeholders is not None
    assert issue.translation_placeholders["details"] == ", ".join(f"Area{n:02}" for n in range(10)) + " +2"


async def test_what_aula_sent_is_reported_when_the_login_fails_too(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aula: FakeAula
) -> None:
    await set_up(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    aula.found = [Anomaly(AnomalyKind.UNREADABLE_RESPONSE, "profiles", dt_util.utcnow())]
    aula.login_error = ParseError("Aula's answer of profiles could not be read")
    await entry.runtime_data.data_coordinator.async_refresh()
    assert not entry.runtime_data.data_coordinator.last_update_success
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"unreadable_response_{entry.entry_id}") is not None
