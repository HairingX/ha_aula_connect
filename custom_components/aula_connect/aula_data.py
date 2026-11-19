"""What a set-up entry holds while it runs."""

from __future__ import annotations

from dataclasses import dataclass

from aula_connect.client import AulaClient
from homeassistant.config_entries import ConfigEntry

from .aula_calendar_coordinator import AulaCalendarCoordinator
from .aula_data_coordinator import AulaDataCoordinator
from .family import FamilyMemory


@dataclass
class AulaRuntimeData:
    """The Aula client of an entry, the coordinators that read Aula with it, and what it remembers."""

    client: AulaClient
    data_coordinator: AulaDataCoordinator
    calendar_coordinator: AulaCalendarCoordinator
    memory: FamilyMemory


type AulaConfigEntry = ConfigEntry[AulaRuntimeData]
"""An Aula Connect config entry, with its runtime data."""
