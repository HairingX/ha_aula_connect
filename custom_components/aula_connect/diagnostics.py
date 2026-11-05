"""Diagnostics of an Aula login: how its reading goes, and Aula's answers with who someone is
replaced; no credentials."""

from __future__ import annotations

import asyncio
import secrets
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .aula_data import AulaConfigEntry
from .const import DOMAIN
from .redact import error_text

READ_DAYS = 21
"""The days from the start of this week that the answers cover: three weeks, as the cards show."""


async def pseudonym_key(hass: HomeAssistant) -> bytes:
    """The secret that replaces each value in Aula's answers by the same one, made once and kept."""
    lock: asyncio.Lock = hass.data.setdefault(f"{DOMAIN}.pseudonym_key_lock", asyncio.Lock())
    async with lock:
        store: Store[dict[str, str]] = Store(hass, 1, f"{DOMAIN}.pseudonym_key", private=True)
        saved = await store.async_load()
        if saved is None or "key" not in saved:
            saved = {"key": secrets.token_hex(32)}
            await store.async_save(saved)
    return bytes.fromhex(saved["key"])


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: AulaConfigEntry) -> dict[str, Any]:
    """How the entry's reading of Aula goes, and everything Aula answers now, with who someone is
    replaced.

    The MitID user ID and the tokens are credentials, and are never included. Each read is made
    again first; every id, name, contact detail and link in the answers is replaced by another of
    its length, the same one each time.
    """
    runtime = entry.runtime_data
    start = dt_util.start_of_local_day() - timedelta(days=dt_util.now().weekday())
    try:
        failed = await runtime.client.read_everything(start, start + timedelta(days=READ_DAYS))
    except Exception as error:
        failed = [error_text(error)]
    answers = runtime.client.responses(await pseudonym_key(hass))
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
        "anomalies": [
            {"kind": anomaly.kind.value, "detail": anomaly.detail, "last_seen": anomaly.last_seen.isoformat()}
            for anomaly in runtime.client.anomalies()
        ],
        "aula": {"not_read": failed, **answers},
    }
