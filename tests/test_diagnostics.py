"""Tests of the diagnostics of an Aula login."""

from __future__ import annotations

import json
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.diagnostics import async_get_config_entry_diagnostics

from .test_init import DATA


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
