"""Home Assistant with this integration loadable, a check that it uses nothing deprecated, and
made-up Aula accounts."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from unittest.mock import patch

from custom_components.aula_connect.aula_calendar_coordinator import AulaCalendarCoordinatorData
from custom_components.aula_connect.aula_data_coordinator import AulaDataCoordinatorData

from .fake_aula import PARENT, FakeAula


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Iterator[None]:
    """Let Home Assistant load the integration from custom_components/."""
    yield


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


@pytest.fixture
def aula_without_children() -> Iterator[None]:
    """An Aula account with no children, no messages and no notifications."""
    data = AulaDataCoordinatorData("Aula", 23, [], [], [], [], [])
    calendar = AulaCalendarCoordinatorData([], [], [], [], [])
    with (
        patch("custom_components.aula_connect.AulaDataCoordinator._connection_check"),
        patch("custom_components.aula_connect.AulaDataCoordinator._fetch_data", return_value=data),
        patch("custom_components.aula_connect.AulaCalendarCoordinator._connection_check"),
        patch("custom_components.aula_connect.AulaCalendarCoordinator._fetch_data", return_value=calendar),
    ):
        yield


@pytest.fixture
def aula() -> Iterator[FakeAula]:
    """A made-up Aula account with one parent and one child, in place of the real client."""
    fake = FakeAula(PARENT)
    with patch("custom_components.aula_connect.AulaClient", return_value=fake):
        yield fake
