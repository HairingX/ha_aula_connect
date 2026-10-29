"""Stress tests of an entry on the real Aula library, against its simulated Aula on a slow and
failing network."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from functools import partial
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aula_connect.client import AulaClient
from aula_connect.testing import SimulatedAulaApi
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from mitid_connect.testing import Network
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.aula_connect.const import DOMAIN

from .test_init import DATA

OVERVIEW: dict[str, object] = {
    "id": 1,
    "institutionProfile": {"id": 11, "institutionCode": "S1", "institutionName": "School"},
    "status": 3,
}


@pytest.fixture
async def aula(socket_enabled: None) -> AsyncIterator[SimulatedAulaApi]:
    """The simulated Aula API in place of the real one, for every client the entry makes.

    Its cookies are kept although it runs on 127.0.0.1, which aiohttp's cookie jar refuses.
    """
    async with SimulatedAulaApi(access_token=str(DATA["access_token"]), network=Network(latency=(0.02, 0.08), seed=7)) as api:
        api.daily_overviews = [OVERVIEW]

        def client(session: aiohttp.ClientSession, **kwargs: Any) -> AulaClient:
            created = AulaClient(session, **kwargs)
            api.point(created.api)
            return created

        with (
            patch("custom_components.aula_connect.AulaClient", client),
            patch("aiohttp.CookieJar", partial(aiohttp.CookieJar, unsafe=True)),
        ):
            yield api


async def _set_up(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account",
        title="Aula",
        data={**DATA, "token_expires_at": time.time() + 3600, "entity_prefix": "Aula"},
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _refresh_for(hass: HomeAssistant, hours: int) -> None:
    """Let `hours` of polling pass, ten minutes at a time, as Home Assistant's clock would."""
    for step in range(1, hours * 6 + 1):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10 * step))
        await hass.async_block_till_done(wait_background_tasks=True)


async def test_reading_aula_on_a_slow_network_never_holds_up_home_assistant(
    hass: HomeAssistant, aula: SimulatedAulaApi
) -> None:
    """While the entry reads Aula for four hours of polling, Home Assistant's event loop keeps
    answering within a few milliseconds."""
    lags: list[float] = []
    stop = asyncio.Event()

    async def tick() -> None:
        while not stop.is_set():
            started = time.monotonic()
            await asyncio.sleep(0.005)
            lags.append(time.monotonic() - started - 0.005)

    ticker = asyncio.create_task(tick())
    entry = await _set_up(hass)
    await _refresh_for(hass, 4)
    stop.set()
    await ticker

    assert aula.network.requests > 50, aula.network.requests
    assert max(lags) < 0.05, max(lags)
    presence = hass.states.get("sensor.aula_presence_alma_school")
    assert presence is not None and presence.state == "present"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_network_that_fails_now_and_then_keeps_the_entities(
    hass: HomeAssistant, aula: SimulatedAulaApi, caplog: pytest.LogCaptureFixture
) -> None:
    """With a tenth of Aula's answers failing, the reads that fail are retried, and the entities
    stay available with what they last read."""
    entry = await _set_up(hass)
    aula.network.failure_rate = 0.1
    availability: list[str] = []
    for step in range(1, 4 * 6 + 1):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10 * step))
        await hass.async_block_till_done(wait_background_tasks=True)
        state = hass.states.get("sensor.aula_presence_alma_school")
        assert state is not None
        availability.append(state.state)

    assert aula.network.failures > 5
    assert set(availability) == {"present"}
    assert "Unexpected error" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "access_token=access" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)
