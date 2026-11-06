"""Tests of the integration on the real Aula library, against the library's simulated Aula: the
MitID login of the setup, and the entities read from Aula's API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aula_connect.client import AulaClient
from aula_connect.login import AulaLoginClient
from aula_connect.testing import SimulatedAulaApi, SimulatedAulaLogin
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.aula_connect.auth_session import auth_session
from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.diagnostics import async_get_config_entry_diagnostics

from .test_login_flow import a_minute_later, run_flow


@pytest.fixture
def local_cookies() -> Iterator[None]:
    """Keep the cookies of the simulated Aula on 127.0.0.1, which aiohttp drops by default."""
    with patch("aiohttp.CookieJar", partial(aiohttp.CookieJar, unsafe=True)):
        yield


@asynccontextmanager
async def set_up(
    hass: HomeAssistant, arrange: Callable[[SimulatedAulaApi], None]
) -> AsyncGenerator[tuple[SimulatedAulaLogin, SimulatedAulaApi]]:
    """Set up an entry through the MitID login against the simulated Aula that `arrange` fills."""
    async with SimulatedAulaLogin() as login, SimulatedAulaApi() as api:
        arrange(api)

        def login_client(session: aiohttp.ClientSession, **kwargs: Any) -> AulaLoginClient:
            return login.point(AulaLoginClient(session, **kwargs))

        def client(session: aiohttp.ClientSession, **kwargs: Any) -> AulaClient:
            aula = AulaClient(session, **kwargs)
            api.access_token = str(kwargs["access_token"])
            api.point(aula.api)
            return aula

        assert await async_setup_component(hass, "http", {})
        with (
            patch("custom_components.aula_connect.config_flow.AulaLoginClient", login_client),
            patch("custom_components.aula_connect.AulaClient", client),
        ):
            result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
            flow_id = str(result["flow_id"])
            flows: Any = hass.config_entries.flow
            step = await flows.async_configure(flow_id, {"id": "Aula", "mitid_username": "parent"})
            assert step["type"] is FlowResultType.EXTERNAL_STEP
            session = auth_session(hass, flow_id)
            assert session is not None and session.task is not None
            async with asyncio.timeout(30):
                await session.task
            assert session.error is None
            assert (await run_flow(hass, flow_id))["type"] is FlowResultType.CREATE_ENTRY
            await hass.async_block_till_done()
        try:
            yield login, api
        finally:
            [entry] = hass.config_entries.async_entries(DOMAIN)
            assert await hass.config_entries.async_unload(entry.entry_id)
            a_minute_later(hass)


@pytest.mark.usefixtures("local_cookies", "socket_enabled")
async def test_a_setup_logs_in_with_mitid_and_reads_aula(hass: HomeAssistant) -> None:
    def arrange(api: SimulatedAulaApi) -> None:
        api.daily_overviews = [
            {"id": 1, "institutionProfile": {"id": 11, "institutionCode": "S1", "institutionName": "School"}, "status": 3}
        ]

    async with set_up(hass, arrange) as (login, api):
        [entry] = hass.config_entries.async_entries(DOMAIN)
        assert entry.data["access_token"] in login.refresh_tokens.values()
        presence = hass.states.get("sensor.aula_presence_alma_school")
        assert presence is not None
        assert presence.state == "present"
        assert hass.states.get("calendar.aula_weekly_plans_alma_school") is not None
        assert "presence.getDailyOverview" in api.requests

        diagnostics: Any = await async_get_config_entry_diagnostics(hass, entry)
        aula = diagnostics["aula"]
        assert aula["not_read"] == [] and aula["aula_api_version"] == SimulatedAulaApi.VERSION
        endpoints = {exchange["endpoint"] for exchange in aula["exchanges"]}
        assert {"profiles.getProfilesByLogin", "presence.getDailyOverview", "/meebook/relatedweekplan/all"} <= endpoints
        text = json.dumps(diagnostics)
        secrets = (entry.data["mitid_username"], entry.data["access_token"], entry.data["refresh_token"])
        for value in ("Alma", "Example", "School", '"alma"', '"pat"', *secrets):
            assert value not in text, value


@pytest.mark.usefixtures("local_cookies", "socket_enabled")
async def test_a_childs_birthday_calendar_holds_only_the_birthdays_aula_ties_to_it(hass: HomeAssistant) -> None:
    def arrange(api: SimulatedAulaApi) -> None:
        birthday = {"institutionProfileId": 31, "birthday": "2017-12-03", "institutionCode": "S1", "mainGroupName": "3A"}
        api.birthdays = [
            {**birthday, "name": "Bo", "relatedChildrenIds": [11]},
            {**birthday, "institutionProfileId": 32, "name": "Liv", "mainGroupName": "0P", "relatedChildrenIds": [12]},
        ]

    async with set_up(hass, arrange):
        start = dt_util.now()
        response: Any = await hass.services.async_call(
            "calendar",
            "get_events",
            {
                "entity_id": "calendar.aula_birthdays_alma_school",
                "start_date_time": start.isoformat(),
                "end_date_time": (start + timedelta(days=366)).isoformat(),
            },
            blocking=True,
            return_response=True,
        )
        summaries = [event["summary"] for event in response["calendar.aula_birthdays_alma_school"]["events"]]
        assert [summary for summary in summaries if "Bo" in summary or "Liv" in summary] == [
            summary for summary in summaries if "Bo" in summary
        ]
        assert any("Bo" in summary for summary in summaries)
