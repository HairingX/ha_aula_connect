"""Tests of the devices of an Aula login: one for the login, and one for each child at each institution."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any, cast

import pytest
from aula_connect.api.models.module import AulaChildProfile, AulaInstitutionProfile
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant.typing import WebSocketGenerator

from custom_components.aula_connect import async_remove_config_entry_device
from custom_components.aula_connect.aula_data import AulaConfigEntry
from custom_components.aula_connect.const import DOMAIN

from .conftest import INSTALLED
from .fake_aula import CHILD, PARENT, FakeAula
from .test_calendars import set_up
from .test_init import DATA

KINDERGARTEN = AulaInstitutionProfile(id=12, institution_code="K1", institution_name="Kindergarten")
ALMA_AT_KINDERGARTEN = replace(CHILD, id=12, institution_code="K1", institution_profile=KINDERGARTEN)
BO = replace(CHILD, first_name="Bo", id=13, name="Bo Example", profile_id=102, user_id="bo")
BO_AT_KINDERGARTEN = replace(BO, id=14, institution_code="K1", institution_profile=KINDERGARTEN)


def _with_children(aula: FakeAula, *children: AulaChildProfile) -> None:
    aula.parent = replace(PARENT, children=list(children))


def _entry(hass: HomeAssistant) -> AulaConfigEntry:
    [entry] = hass.config_entries.async_entries(DOMAIN)
    return entry


def _device(hass: HomeAssistant, child: AulaChildProfile) -> dr.ChildDeviceEntry:
    entry_id = _entry(hass).entry_id
    device = dr.async_get(hass).async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_{child.id}"), entry_id)
    assert device is not None
    return device


def _entity_ids(hass: HomeAssistant, device_id: str) -> list[str]:
    return sorted(entity.entity_id for entity in er.async_entries_for_device(er.async_get(hass), device_id))


async def _refresh(hass: HomeAssistant) -> None:
    await _entry(hass).runtime_data.data_coordinator.async_refresh()
    await hass.async_block_till_done()


async def _disable(hass: HomeAssistant, child: AulaChildProfile) -> None:
    dr.async_get(hass).async_update_child_device(_device(hass, child).id, disabled_by=dr.DeviceEntryDisabler.USER)
    await hass.async_block_till_done()


@pytest.mark.fresh_install
async def test_each_child_at_each_institution_is_a_device_under_the_login(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    _with_children(aula, CHILD, ALMA_AT_KINDERGARTEN)
    await set_up(hass, freezer)
    registry = dr.async_get(hass)
    login = registry.async_get_device_by_identifier((DOMAIN, "Aula"), _entry(hass).entry_id)
    assert login is not None and login.name == "Aula" and login.manufacturer == "Aula"
    school, kindergarten = _device(hass, CHILD), _device(hass, ALMA_AT_KINDERGARTEN)
    assert (school.name, school.parent_device_id) == ("Alma · School", login.id)
    assert (kindergarten.name, kindergarten.parent_device_id) == ("Alma · Kindergarten", login.id)
    assert "sensor.aula_alma_school_presence" in _entity_ids(hass, school.id)
    assert "sensor.aula_alma_kindergarten_presence" in _entity_ids(hass, kindergarten.id)
    assert _entity_ids(hass, login.id) == [
        "binary_sensor.aula_unread_calendar",
        "binary_sensor.aula_unread_gallery",
        "binary_sensor.aula_unread_message",
        "binary_sensor.aula_unread_post",
        "binary_sensor.aula_unread_presence",
        "calendar.aula_events_pat_school",
        "event.aula_new_event",
        "event.aula_new_message",
        "event.aula_new_photo",
    ]


async def test_an_installation_keeps_every_entity_id_and_unique_id_on_the_childs_device(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    registry = er.async_get(hass)
    child_device = _device(hass, CHILD)
    for entity_id, unique_id in INSTALLED.items():
        entity = registry.async_get(entity_id)
        assert entity is not None and entity.unique_id == unique_id, entity_id
        on_child = unique_id.endswith("_Alma_School")
        assert (entity.device_id == child_device.id) is on_child, entity_id
    presence = hass.states.get("sensor.aula_presence_alma_school")
    assert presence is not None and presence.attributes["friendly_name"] == "Alma · School Presence"
    unread = hass.states.get("binary_sensor.aula_unread_message")
    assert unread is not None and unread.attributes["friendly_name"] == "Aula Unread Message"
    parent = hass.states.get("calendar.aula_events_pat_school")
    assert parent is not None and parent.attributes["friendly_name"] == "Aula Events Pat School"


CHILD_KEYS = {
    "en": [
        "binary_sensor.{}_school_today",
        "binary_sensor.{}_school_tomorrow",
        "binary_sensor.{}_vacation_request",
        "calendar.{}_birthdays",
        "calendar.{}_easyiq_weekly_plans",
        "calendar.{}_events",
        "calendar.{}_newsletter",
        "calendar.{}_weekly_plans",
        "image.{}_picture",
        "sensor.{}_presence",
        "sensor.{}_present_duration",
        "sensor.{}_school_end",
        "sensor.{}_school_start",
        "sensor.{}_status",
        "todo.{}_homework",
    ],
    "da": [
        "binary_sensor.{}_ferieanmodning",
        "binary_sensor.{}_skole_i_dag",
        "binary_sensor.{}_skole_i_morgen",
        "calendar.{}_begivenheder",
        "calendar.{}_easyiq_ugeplaner",
        "calendar.{}_fodselsdage",
        "calendar.{}_ugebrev",
        "calendar.{}_ugeplaner",
        "image.{}_billede",
        "sensor.{}_skoleslut",
        "sensor.{}_skolestart",
        "sensor.{}_status",
        "sensor.{}_tilstede",
        "sensor.{}_tilstede_varighed",
        "todo.{}_lektier",
    ],
}
"""A new installation's entity ids of a child's device, by language, its device's part in place of `{}`."""

LOGIN_IDS = {
    "en": [
        "binary_sensor.aula_unread_calendar",
        "binary_sensor.aula_unread_gallery",
        "binary_sensor.aula_unread_message",
        "binary_sensor.aula_unread_post",
        "binary_sensor.aula_unread_presence",
        "calendar.aula_events_pat_school",
        "event.aula_new_event",
        "event.aula_new_message",
        "event.aula_new_photo",
    ],
    "da": [
        "binary_sensor.aula_ulaest_besked",
        "binary_sensor.aula_ulaest_galleri",
        "binary_sensor.aula_ulaest_kalender",
        "binary_sensor.aula_ulaest_opslag",
        "binary_sensor.aula_ulaest_tilstedevaerelse",
        "calendar.aula_begivenheder_pat_school",
        "event.aula_ny_begivenhed",
        "event.aula_ny_besked",
        "event.aula_nyt_billede",
    ],
}
"""A new installation's entity ids of the login's own device, by language."""


@pytest.mark.fresh_install
@pytest.mark.parametrize("language", ["en", "da"])
async def test_a_new_installation_names_the_entity_ids_after_the_login_the_child_and_the_institution(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, language: str
) -> None:
    hass.config.language = language
    _with_children(aula, CHILD, BO, BO_AT_KINDERGARTEN)
    await set_up(hass, freezer)
    found = {_device(hass, child).name: _entity_ids(hass, _device(hass, child).id) for child in (CHILD, BO, BO_AT_KINDERGARTEN)}
    assert found == {
        name: [key.format(part) for key in CHILD_KEYS[language]]
        for name, part in [
            ("Alma · School", "aula_alma_school"),
            ("Bo · School", "aula_bo_school"),
            ("Bo · Kindergarten", "aula_bo_kindergarten"),
        ]
    }
    login = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "Aula"), _entry(hass).entry_id)
    assert login is not None
    assert _entity_ids(hass, login.id) == LOGIN_IDS[language]


async def _children_listed(hass: HomeAssistant, client: Any) -> list[int]:
    await client.send_json_auto_id({"type": "aula_connect/children"})
    result = await client.receive_json()
    return [child["child_id"] for child in result["result"]["children"]]


@pytest.mark.fresh_install
async def test_a_disabled_childs_device_is_not_read_listed_or_acted_for_and_comes_back_enabled(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WebSocketGenerator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert await async_setup_component(hass, "calendar", {})
    client = await hass_ws_client(hass)
    _with_children(aula, CHILD, ALMA_AT_KINDERGARTEN)
    await set_up(hass, freezer)
    assert hass.states.get("sensor.aula_alma_kindergarten_presence") is not None

    await _disable(hass, ALMA_AT_KINDERGARTEN)
    entry = _entry(hass)
    assert [child.id for child in entry.runtime_data.data_coordinator.data.children] == [CHILD.id]
    assert hass.states.get("sensor.aula_alma_kindergarten_presence") is None
    assert hass.states.get("sensor.aula_alma_school_presence") is not None
    assert await _children_listed(hass, client) == [CHILD.id]
    await client.send_json_auto_id({"type": "aula_connect/sick/report", "entry_id": entry.entry_id, "child_id": 12})
    answer = await client.receive_json()
    assert answer["error"]["code"] == "not_found"
    assert not aula.writes

    aula.asked.clear()
    freezer.tick(timedelta(days=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert CHILD.id in aula.asked and ALMA_AT_KINDERGARTEN.id not in aula.asked

    aula.asked.clear()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert CHILD.id in aula.asked and ALMA_AT_KINDERGARTEN.id not in aula.asked
    assert hass.states.get("sensor.aula_alma_kindergarten_presence") is None
    kept = er.async_get(hass).async_get("sensor.aula_alma_kindergarten_presence")
    assert kept is not None and kept.disabled_by is er.RegistryEntryDisabler.DEVICE

    dr.async_get(hass).async_update_child_device(_device(hass, ALMA_AT_KINDERGARTEN).id, disabled_by=None)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert "Reloading configuration entries because disabled_by changed" in caplog.text
    assert [child.id for child in _entry(hass).runtime_data.data_coordinator.data.children] == [CHILD.id, 12]
    assert hass.states.get("sensor.aula_alma_kindergarten_presence") is not None


@pytest.mark.fresh_install
async def test_a_device_of_a_child_aula_no_longer_lists_is_removed_but_a_disabled_one_is_kept(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    _with_children(aula, CHILD, ALMA_AT_KINDERGARTEN, BO)
    await set_up(hass, freezer)
    await _disable(hass, BO)
    registry = dr.async_get(hass)
    entry = _entry(hass)
    school, bo = _device(hass, CHILD), _device(hass, BO)

    _with_children(aula, CHILD)
    await _refresh(hass)
    assert registry.async_get_child_device_by_identifier((DOMAIN, f"{entry.entry_id}_12"), entry.entry_id) is None
    assert er.async_get(hass).async_get("sensor.aula_alma_kindergarten_presence") is None
    assert hass.states.get("sensor.aula_alma_kindergarten_presence") is None
    assert registry.async_get(bo.id) is not None
    assert hass.states.get("sensor.aula_alma_school_presence") is not None

    login = registry.async_get_device_by_identifier((DOMAIN, "Aula"), entry.entry_id)
    assert login is not None
    assert not await async_remove_config_entry_device(hass, entry, login)
    assert not await async_remove_config_entry_device(hass, entry, school)
    assert await async_remove_config_entry_device(hass, entry, bo)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not await async_remove_config_entry_device(hass, entry, bo)


@pytest.mark.fresh_install
async def test_a_child_aula_lists_later_gets_its_device_and_entities_without_a_reload(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    assert hass.states.get("sensor.aula_bo_school_presence") is None

    _with_children(aula, CHILD, BO)
    await _refresh(hass)
    assert _entity_ids(hass, _device(hass, BO).id) == [key.format("aula_bo_school") for key in CHILD_KEYS["en"]]
    assert hass.states.get("sensor.aula_bo_school_presence") is not None

    _with_children(aula, CHILD)
    await _refresh(hass)
    assert hass.states.get("sensor.aula_bo_school_presence") is None

    _with_children(aula, CHILD, BO)
    await _refresh(hass)
    assert hass.states.get("sensor.aula_bo_school_presence") is not None
    assert _entry(hass).state is ConfigEntryState.LOADED


async def test_disabling_a_childs_device_of_one_login_leaves_the_child_of_another_login_read(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    [first] = hass.config_entries.async_entries(DOMAIN)
    second = MockConfigEntry(
        domain=DOMAIN, unique_id="other", title="Aula 2", data={**DATA, "entity_prefix": "Aula 2"}, version=2, minor_version=2
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    device = registry.async_get_child_device_by_identifier((DOMAIN, f"{first.entry_id}_11"), first.entry_id)
    assert device is not None
    registry.async_update_child_device(device.id, disabled_by=dr.DeviceEntryDisabler.USER)
    await hass.async_block_till_done()
    assert cast(AulaConfigEntry, first).runtime_data.data_coordinator.data.children == []
    assert [child.id for child in cast(AulaConfigEntry, second).runtime_data.data_coordinator.data.children] == [CHILD.id]


async def test_a_renamed_login_keeps_its_childrens_devices_and_which_are_disabled(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    entry = _entry(hass)
    registry = dr.async_get(hass)
    device = _device(hass, CHILD)
    registry.async_update_child_device(device.id, disabled_by=dr.DeviceEntryDisabler.USER)
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(entry, title="Familien")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    kept = _device(hass, CHILD)
    assert (kept.id, kept.disabled) == (device.id, True)
    assert cast(AulaConfigEntry, entry).runtime_data.data_coordinator.data.children == []
