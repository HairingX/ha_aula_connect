"""Tests of reading one method of Aula's API through the action, and of asking for the diagnostics
when Aula sends what the integration does not know."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from aula_connect import Anomaly, AnomalyKind
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

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


async def _read(hass: HomeAssistant, entry_id: str, method: str = "profiles.getProfileContext") -> Any:
    return await hass.services.async_call(
        DOMAIN,
        "read",
        {"config_entry_id": entry_id, "method": method, "params": {"portalrole": "guardian"}},
        blocking=True,
        return_response=True,
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
    with pytest.raises(ServiceValidationError) as unknown:
        await _read(hass, "no-such-entry")
    assert unknown.value.translation_key == "entry_not_loaded"
    client.read.side_effect = ValueError("not a get")
    with pytest.raises(ServiceValidationError) as refused:
        await _read(hass, entry.entry_id, "messaging.deleteThread")
    assert refused.value.translation_placeholders == {"method": "messaging.deleteThread"}


@pytest.mark.usefixtures("aula_without_children")
async def test_the_action_says_why_aula_could_not_be_read_without_its_token(hass: HomeAssistant) -> None:
    entry = await _entry(hass)
    client: Any = entry.runtime_data.client
    client.read.side_effect = ConnectionError("url: /api?access_token=s3cr3t")
    with pytest.raises(HomeAssistantError) as failed:
        await _read(hass, entry.entry_id)
    assert failed.value.translation_placeholders == {"error": "url: /api?access_token=REDACTED"}


async def test_aula_sending_what_is_not_known_asks_for_the_diagnostics_a_kind_an_issue_until_it_stops(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aula: FakeAula
) -> None:
    await set_up(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    coordinator = entry.runtime_data.data_coordinator
    issues = ir.async_get(hass)
    unknown = f"unknown_notification_area_{coordinator.device_id}"
    version = f"api_version_changed_{coordinator.device_id}"
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


async def test_unloading_an_entry_takes_its_issues_away(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aula: FakeAula
) -> None:
    await set_up(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    aula.found = [Anomaly(AnomalyKind.UNREADABLE_RESPONSE, "notifications", dt_util.utcnow())]
    await entry.runtime_data.data_coordinator.async_refresh()
    issue = f"unreadable_response_{entry.runtime_data.data_coordinator.device_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None
