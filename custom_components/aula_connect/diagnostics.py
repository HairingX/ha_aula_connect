"""Diagnostics of an Aula login: how its reading goes, and Aula's answers with who someone is
replaced; no credentials."""

from __future__ import annotations

import asyncio
import secrets
from datetime import timedelta
from typing import Any

from aula_connect.api.models.module import AulaChildProfile
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey
from homeassistant.util import dt as dt_util

from . import family_actions
from .aula_data import AulaConfigEntry
from .const import DOMAIN
from .redact import error_text

READ_DAYS = 21
"""The days from the start of this week that the answers cover: three weeks, as the cards show."""


_KEY_LOCK: HassKey[asyncio.Lock] = HassKey(f"{DOMAIN}.pseudonym_key_lock")


async def pseudonym_key(hass: HomeAssistant) -> bytes:
    """The secret that replaces each value in Aula's answers by the same one, made once and kept."""
    if _KEY_LOCK not in hass.data:
        hass.data[_KEY_LOCK] = asyncio.Lock()
    async with hass.data[_KEY_LOCK]:
        store: Store[dict[str, str]] = Store(hass, 1, f"{DOMAIN}.pseudonym_key", private=True)
        saved = await store.async_load()
        if saved is None or "key" not in saved:
            saved = {"key": secrets.token_hex(32)}
            await store.async_save(saved)
    return bytes.fromhex(saved["key"])


async def _check(hass: HomeAssistant, entry: AulaConfigEntry, child: AulaChildProfile) -> dict[str, Any]:
    """What reporting the child sick would do, as yes and no alone: none of the child's data."""
    try:
        check = await family_actions.check_sick_report(hass, entry, child)
    except family_actions.FamilyActionError as err:
        return {"error": err.translation_key}
    return {key: check[key] for key in ("route", "child_found", "same_institution", "sick_today")}


async def _posts_seen(entry: AulaConfigEntry) -> dict[str, Any]:
    """Whether reading the news feed moves when Aula says the guardian last saw it: the newest post
    read twice, a moment apart, and when Aula said they were last seen each time."""
    client = entry.runtime_data.client
    try:
        profiles = (await client.login()).profiles
        first_read_at = dt_util.utcnow()
        first = await client.get_posts(profiles, 1)
        second = await client.get_posts(profiles, 1)
    except Exception as error:
        return {"error": error_text(error)}
    return {
        "first_read_at": first_read_at.isoformat(),
        "last_seen_before": first.last_seen.isoformat() if first.last_seen else None,
        "last_seen_after": second.last_seen.isoformat() if second.last_seen else None,
        "moved": first.last_seen != second.last_seen,
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: AulaConfigEntry) -> dict[str, Any]:
    """How the entry's reading of Aula goes, and everything Aula answers now, with who someone is
    replaced.

    The MitID user ID and the tokens are credentials, and are never included. Each read is made
    again first, and the news feed twice more after, to tell whether reading it moves when Aula
    says it was last seen; every id, name, contact detail and link in the answers is replaced by
    another of its length, the same one each time. The report of the latest probe of the widgets' services,
    where an administrator made one, is added as it is.
    """
    runtime = entry.runtime_data
    start = dt_util.start_of_local_day() - timedelta(days=dt_util.now().weekday())
    try:
        failed = await runtime.client.read_everything(start, start + timedelta(days=READ_DAYS))
    except Exception as error:
        failed = [error_text(error)]
    answers = runtime.client.responses(await pseudonym_key(hass))
    posts_seen = await _posts_seen(entry)
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
        "posts_seen": posts_seen,
        "sick_report_checks": [await _check(hass, entry, child) for child in (data.children if data else [])],
        # The probe's report names steps, fields and cookies, never a value of one.
        "widget_probe": None if runtime.client.widget_probe is None else runtime.client.widget_probe.as_dict(),
    }
