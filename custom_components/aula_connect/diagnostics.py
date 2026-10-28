"""Diagnostics of an Aula login, without credentials and without the children's data."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .aula_data import AulaConfigEntry
from .redact import error_text


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: AulaConfigEntry) -> dict[str, Any]:
    """How the entry's reading of Aula goes, as counts and states only.

    The MitID user ID and the tokens are credentials, and what Aula holds about the children is
    personal data, so neither is included: only whether reads succeed and how much they found.
    """
    runtime = entry.runtime_data
    data_coordinator = runtime.data_coordinator
    calendar_coordinator = runtime.calendar_coordinator
    data = data_coordinator.data
    return {
        "entry": {"version": entry.version, "minor_version": entry.minor_version, "state": entry.state.value},
        "aula_api_version": data_coordinator.aula_version,
        "data": {
            "last_update_success": data_coordinator.last_update_success,
            "last_exception": error_text(data_coordinator.last_exception) if data_coordinator.last_exception else None,
            "profiles": len(data.profiles) if data else None,
            "children": len(data.children) if data else None,
            "daily_overviews": len(data.daily_overviews) if data else None,
            "message_threads": len(data.message_threads) if data else None,
            "notifications": len(data.notifications) if data else None,
        },
        "widgets": {
            "weekly_plans": data_coordinator.weekly_plans_supported(),
            "easyiq_weekplan": data_coordinator.easyiq_weekplan_supported(),
            "reminders": data_coordinator.reminders_supported(),
            "newsletter": data_coordinator.newsletter_supported(),
            "assignments": data_coordinator.assignments_supported(),
        },
        "calendar": {
            "last_update_success": calendar_coordinator.last_update_success,
            "last_exception": (
                error_text(calendar_coordinator.last_exception) if calendar_coordinator.last_exception else None
            ),
        },
    }
