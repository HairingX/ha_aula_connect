"""Tests that the README names the entities and attributes the integration has, and no others."""

from __future__ import annotations

import re
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from aula_connect.api.models.module import AulaDailyOverview

from custom_components.aula_connect.const import DOMAIN

from .fake_aula import SCHOOL, FakeAula
from .test_calendars import set_up
from .test_family_entities import message_thread, photo_and_event

README = Path(__file__).parent.parent / "README.md"
ROW = re.compile(r"^\| `(?P<entity>[a-z_]+\.[a-z_<>]+)` \|[^|]*\|(?P<attributes>[^|]*)\|$", re.MULTILINE)
ENTITY_ATTRIBUTES = {
    "friendly_name",
    "icon",
    "device_class",
    "unit_of_measurement",
    "state_class",
    "options",
    "supported_features",
    "event_types",
    "access_token",
    "entity_picture",
}
CALENDAR_ATTRIBUTES = {"message", "all_day", "start_time", "end_time", "location", "description", "offset_reached"}


def _documented() -> dict[str, set[str]]:
    return {
        row["entity"]: set(re.findall(r"`([a-z_]+)`", row["attributes"])) for row in ROW.finditer(README.read_text())
    }


async def test_the_readme_lists_every_entity_with_its_attributes(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.daily_overviews = [AulaDailyOverview(id=1, institution_profile=SCHOOL, status=3)]
    await set_up(hass, freezer)
    aula.message_threads = [message_thread(1, "m1")]
    aula.notifications = [*photo_and_event()]
    [entry] = hass.config_entries.async_entries(DOMAIN)
    await entry.runtime_data.data_coordinator.async_refresh()
    await hass.async_block_till_done()
    actual = {
        state.entity_id.replace("alma_school", "<child>_<institution>").replace(
            "pat_school", "<parent>_<institution>"
        ): set(state.attributes) - ENTITY_ATTRIBUTES - (CALENDAR_ATTRIBUTES if state.domain == "calendar" else set())
        for state in hass.states.async_all()
        if state.entity_id.split(".")[1].startswith("aula_")
    }
    assert _documented() == actual
