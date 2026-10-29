"""Tests of the sensors and binary sensors of an Aula login, read from a made-up Aula account."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aula_connect.api.models.module import AulaDailyOverview
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .fake_aula import SCHOOL, FakeAula
from .test_calendars import set_up

TRANSLATIONS = Path(__file__).parent.parent / "custom_components" / "aula_connect" / "translations"
COMMON_ATTRIBUTES = {"friendly_name", "icon", "device_class", "unit_of_measurement", "state_class", "options"}


@pytest.mark.parametrize(
    ("status", "presence"),
    [(0, "not_present"), (1, "not_present"), (2, "not_present"), (3, "present"), (8, "not_present"), (9, "unknown")],
    ids=["not arrived", "sick", "reported absent", "present", "checked out", "unknown"],
)
async def test_presence_says_whether_the_child_is_at_the_institution(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, status: int, presence: str
) -> None:
    aula.daily_overviews = [AulaDailyOverview(id=1, institution_profile=SCHOOL, status=status)]
    await set_up(hass, freezer)
    state = hass.states.get("sensor.aula_presence_alma_school")
    assert state is not None
    assert state.state == presence
    status_state = hass.states.get("sensor.aula_status_alma_school")
    assert status_state is not None
    assert status_state.state == str(status)


@pytest.mark.parametrize("language", ["en", "da"])
async def test_every_attribute_has_a_name(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, language: str
) -> None:
    await set_up(hass, freezer)
    translations = json.loads((TRANSLATIONS / f"{language}.json").read_text())["entity"]
    registry = er.async_get(hass)
    for state in hass.states.async_all(["sensor", "binary_sensor"]):
        entry = registry.async_get(state.entity_id)
        assert entry is not None and entry.translation_key is not None
        named = translations[state.domain][entry.translation_key].get("state_attributes", {})
        assert set(state.attributes) - COMMON_ATTRIBUTES <= set(named), state.entity_id
