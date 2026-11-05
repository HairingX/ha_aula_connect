"""Tests of the diagnostics of an Aula login."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from aula_connect import Anomaly, AnomalyKind
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.diagnostics import async_get_config_entry_diagnostics

from .test_init import DATA


@pytest.mark.usefixtures("aula_without_children")
async def test_the_diagnostics_name_what_aula_sent_that_is_not_known(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="account", data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    seen = datetime(2026, 12, 28, 8, tzinfo=timezone.utc)
    entry.runtime_data.client.anomalies.return_value = (Anomaly(AnomalyKind.UNKNOWN_NOTIFICATION_AREA, "Homework", seen),)

    diagnostics: Any = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["anomalies"] == [
        {"kind": "unknown_notification_area", "detail": "Homework", "last_seen": "2026-12-28T08:00:00+00:00"}
    ]


@pytest.mark.usefixtures("aula_without_children")
async def test_the_diagnostics_hold_no_credentials_and_no_childrens_data(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account",
        title="Family",
        data={**DATA, "entity_prefix": "Family"},
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics: Any = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["data"] == {
        "last_update_success": True,
        "last_exception": None,
        "profiles": 0,
        "children": 0,
        "daily_overviews": 0,
        "message_threads": 0,
        "notifications": 0,
    }
    assert diagnostics["calendar"]["last_update_success"] is True
    assert diagnostics["anomalies"] == []
    assert diagnostics["aula"] == {"not_read": [], "aula_api_version": 23, "exchanges": [], "shapes": {}}
    text = json.dumps(diagnostics)
    for secret in ("parent", "access", "refresh", "Family", "account"):
        assert secret not in text


@pytest.mark.usefixtures("aula_without_children")
async def test_the_diagnostics_show_the_last_error_without_its_token(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="account", data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.data_coordinator.last_exception = ConnectionError("url: /api?access_token=s3cr3t")

    diagnostics: Any = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["data"]["last_exception"] == "url: /api?access_token=REDACTED"


@pytest.mark.usefixtures("aula_without_children")
async def test_the_diagnostics_read_this_week_and_two_more_and_say_what_they_could_not(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="account", data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client: Any = entry.runtime_data.client
    client.read_everything.side_effect = ConnectionError("url: /api?access_token=s3cr3t")

    diagnostics: Any = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["aula"]["not_read"] == ["url: /api?access_token=REDACTED"]
    start, end = client.read_everything.call_args.args
    assert (start.weekday(), start.hour, (end - start).days) == (0, 0, 21)


@pytest.mark.usefixtures("aula_without_children")
async def test_the_diagnostics_replace_with_one_key_made_once_and_kept_private(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="account", data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client: Any = entry.runtime_data.client

    await async_get_config_entry_diagnostics(hass, entry)
    await async_get_config_entry_diagnostics(hass, entry)

    [first], [second] = (call.args for call in client.responses.call_args_list)
    assert first == second and len(first) == 32
    assert hass_storage["aula_connect.pseudonym_key"]["data"] == {"key": first.hex()}
