"""Home Assistant with this integration loadable, a check that it uses nothing deprecated, and
made-up Aula accounts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from aula_connect import read_marks
from homeassistant.helpers import entity_registry as er
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.aula_connect.aula_calendar_coordinator import AulaCalendarCoordinatorData
from custom_components.aula_connect.aula_data_coordinator import AulaDataCoordinatorData

from custom_components.aula_connect.const import DOMAIN

from .fake_aula import PARENT, FakeAula

INSTALLED = {
    "binary_sensor.aula_school_today_alma_school": "Aula_school_today_Alma_School",
    "binary_sensor.aula_school_tomorrow_alma_school": "Aula_school_tomorrow_Alma_School",
    "binary_sensor.aula_unread_calendar": "Aula_unread_calendar",
    "binary_sensor.aula_unread_gallery": "Aula_unread_gallery",
    "binary_sensor.aula_unread_message": "Aula_unread_message",
    "binary_sensor.aula_unread_post": "Aula_unread_post",
    "binary_sensor.aula_unread_presence": "Aula_unread_presence",
    "binary_sensor.aula_vacation_request_alma_school": "Aula_vacation_request_Alma_School",
    "calendar.aula_birthdays_alma_school": "Aula_birthdays_Alma_School",
    "calendar.aula_easyiq_weekly_plans_alma_school": "Aula_easyiq_weekplan_Alma_School",
    "calendar.aula_events_alma_school": "Aula_events_Alma_School",
    "calendar.aula_events_pat_school": "Aula_events_Pat_School",
    "calendar.aula_newsletter_alma_school": "Aula_newsletter_Alma_School",
    "calendar.aula_weekly_plans_alma_school": "Aula_weekly_plan_Alma_School",
    "event.aula_new_event": "Aula_new_event",
    "event.aula_new_message": "Aula_new_message",
    "event.aula_new_photo": "Aula_new_photo",
    "image.aula_picture_alma_school": "Aula_picture_Alma_School",
    "sensor.aula_presence_alma_school": "Aula_presence_Alma_School",
    "sensor.aula_present_duration_alma_school": "Aula_presence_duration_Alma_School",
    "sensor.aula_school_end_alma_school": "Aula_school_end_Alma_School",
    "sensor.aula_school_start_alma_school": "Aula_school_start_Alma_School",
    "sensor.aula_status_alma_school": "Aula_status_Alma_School",
    "todo.aula_homework_alma_school": "Aula_homework_Alma_School",
}
"""Every entity an installation of the made-up account had before each child got a device, by its entity id, with its unique id."""

INSTALLED_SECOND = {
    entity_id.replace(".aula_", ".aula_2_"): unique_id.replace("Aula_", "Aula 2_", 1) for entity_id, unique_id in INSTALLED.items()
}
"""The same, for a second login set up under the name `Aula 2`."""

MIGRATED = {
    entity_id.replace("_homework_", "_important_"): unique_id.replace("_homework_", "_important_")
    for entity_id, unique_id in INSTALLED.items()
}
"""Every entity of INSTALLED once the entry is up to date: the list of homework renamed to what is important."""


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Iterator[None]:
    """Let Home Assistant load the integration from custom_components/."""
    yield


@pytest.fixture(autouse=True)
def no_pause_before_reading_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read a part of Aula once more at once, so that the tests take no real pause."""
    monkeypatch.setattr("custom_components.aula_connect.retry.RETRY_DELAY", timedelta(0))


@pytest.fixture(autouse=True)
def no_deprecated_usage(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Fail a test in which Home Assistant reports this integration using something deprecated."""
    yield
    reports = [
        record.getMessage()
        for phase in ("setup", "call")
        for record in caplog.get_records(phase)
        if "Detected that custom integration 'aula_connect'" in record.getMessage()
        or " was used from aula_connect" in record.getMessage()
    ]
    assert reports == []


@pytest.fixture(autouse=True)
def installed_before(request: pytest.FixtureRequest) -> None:
    """Register the made-up account's entities as an installation from before had them, unless the test is a fresh installation."""
    if "hass" not in request.fixturenames or "fresh_install" in request.keywords:
        return
    registry = er.async_get(request.getfixturevalue("hass"))
    for entity_id, unique_id in {**INSTALLED, **INSTALLED_SECOND}.items():
        domain, object_id = entity_id.split(".")
        registry.async_get_or_create(domain, DOMAIN, unique_id, suggested_object_id=object_id)


@pytest.fixture(autouse=True)
def no_wait_to_read_marks_again() -> Iterator[None]:
    """Read Aula's marks of what was seen again at once in the diagnostics, not seconds later."""
    with patch.object(read_marks, "RECHECK_AFTER", timedelta(0)):
        yield


@pytest.fixture
def aula_without_children() -> Iterator[None]:
    """An Aula account with no children, no messages and no notifications."""
    data = AulaDataCoordinatorData("Aula", 23, [], [], [], [], [])
    calendar = AulaCalendarCoordinatorData([], [], [], [], [])
    client = MagicMock(
        connection_check=AsyncMock(),
        aula_version=23,
        has_widget=MagicMock(return_value=False),
        read_everything=AsyncMock(return_value=[]),
        anomalies=MagicMock(return_value=()),
        widget_probe=None,
        read=AsyncMock(return_value={"endpoint": "profiles.getProfileContext"}),
        responses=MagicMock(return_value={"aula_api_version": 23, "exchanges": [], "shapes": {}}),
    )
    with (
        patch("custom_components.aula_connect.AulaClient", return_value=client),
        patch("custom_components.aula_connect.AulaDataCoordinator._fetch_data", AsyncMock(return_value=data)),
        patch("custom_components.aula_connect.AulaCalendarCoordinator._fetch_data", AsyncMock(return_value=calendar)),
    ):
        yield


@pytest.fixture
def aula() -> Iterator[FakeAula]:
    """A made-up Aula account with one parent and one child, in place of the real client."""
    fake = FakeAula(PARENT)
    with patch("custom_components.aula_connect.AulaClient", return_value=fake):
        yield fake
