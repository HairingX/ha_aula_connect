"""Tests that the README names the entities and attributes the integration has, and no others."""

from __future__ import annotations

import re
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from .fake_aula import FakeAula
from .test_calendars import set_up

README = Path(__file__).parent.parent / "README.md"
ROW = re.compile(r"^\| `(?P<entity>[a-z_]+\.[a-z_<>]+)` \|[^|]*\|(?P<attributes>[^|]*)\|$", re.MULTILINE)
ENTITY_ATTRIBUTES = {"friendly_name", "icon", "device_class", "unit_of_measurement", "state_class", "options"}
CALENDAR_ATTRIBUTES = {"message", "all_day", "start_time", "end_time", "location", "description", "offset_reached"}


def _documented() -> dict[str, set[str]]:
    return {
        row["entity"]: set(re.findall(r"`([a-z_]+)`", row["attributes"])) for row in ROW.finditer(README.read_text())
    }


async def test_the_readme_lists_every_entity_with_its_attributes(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    actual = {
        state.entity_id.replace("alma_school", "<child>_<institution>").replace(
            "pat_school", "<parent>_<institution>"
        ): set(state.attributes) - ENTITY_ATTRIBUTES - (CALENDAR_ATTRIBUTES if state.domain == "calendar" else set())
        for state in hass.states.async_all()
        if state.entity_id.split(".")[1].startswith("aula_")
    }
    assert _documented() == actual
