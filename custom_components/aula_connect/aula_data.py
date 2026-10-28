"""What a set-up entry holds while it runs."""

from __future__ import annotations

from dataclasses import dataclass

from aula_connect.client import AulaClient
from homeassistant.config_entries import ConfigEntry

from .aula_calendar_coordinator import AulaCalendarCoordinator
from .aula_data_coordinator import AulaDataCoordinator


@dataclass
class AulaRuntimeData:
    """The Aula client of an entry and the coordinators that read Aula with it."""

    client: AulaClient
    data_coordinator: AulaDataCoordinator
    calendar_coordinator: AulaCalendarCoordinator


type AulaConfigEntry = ConfigEntry[AulaRuntimeData]
"""An Aula Connect config entry, with its runtime data."""
