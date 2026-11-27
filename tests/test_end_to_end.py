"""Tests of the integration on the real Aula library, against the library's simulated Aula: the
MitID login of the setup, and the entities read from Aula's API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from functools import partial
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aula_connect.client import AulaClient
from aula_connect.login import AulaLoginClient
from aula_connect.testing import SimulatedAulaApi, SimulatedAulaLogin
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.aula_connect.auth_session import auth_session
from custom_components.aula_connect.const import ALLOWED_CHANGES, DOMAIN
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
        assert aula["aula_api_version"] == SimulatedAulaApi.VERSION
        assert [line for line in aula["not_read"] if ": not asked, as " not in line] == []
        assert "search.findGroups: not asked, as it searches for a text" in aula["not_read"]
        assert [(mark["method"], mark["moved"]) for mark in aula["read_marks"]] == [
            ("posts.getAllPosts", 0),
            ("messaging.getThreads", 0),
            ("notifications.getNotificationsForActiveProfile", 0),
        ]
        endpoints = {exchange["endpoint"] for exchange in aula["exchanges"]}
        assert {
            "profiles.getProfilesByLogin",
            "presence.getDailyOverview",
            "/aulaapi/relatedweekplan/all",
            "posts.getAllPosts",
            "posts.getById",
            "gallery.getAlbums",
        } <= endpoints
        text = json.dumps(diagnostics)
        username = entry.data["mitid_username"]
        secrets = (f'"{username}"', f"={username}", entry.data["access_token"], entry.data["refresh_token"])
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


def _next_monday(weeks: int) -> date:
    today = dt_util.now().date()
    return today + timedelta(days=7 * weeks - today.weekday())


@pytest.mark.usefixtures("local_cookies", "socket_enabled")
async def test_a_guardian_reports_sick_registers_a_vacation_answers_a_request_and_replies_in_aula(
    hass: HomeAssistant,
) -> None:
    monday = _next_monday(2)

    def arrange(api: SimulatedAulaApi) -> None:
        school = {"id": 11, "institutionCode": "S1", "institutionName": "School"}
        api.daily_overviews = [{"id": 1, "institutionProfile": school, "status": 0}]
        api.vacation_requests = [
            {
                "responseId": 501,
                "vacationRegistrationId": 50,
                "childId": 11,
                "title": "Juleferie",
                "startDate": monday.isoformat(),
                "endDate": (monday + timedelta(days=4)).isoformat(),
                "responseDeadline": (monday - timedelta(days=1)).isoformat(),
                "noteToGuardian": "Sig til",
                "isEditable": True,
                "isPresenceTimesRequired": True,
            }
        ]
        api.threads = [
            {
                "id": 7,
                "creator": {"answerDirectlyName": "Lærer", "fullName": "Lærer", "metadata": "Skole"},
                "subject": "Klassen",
                "regardingChildren": [{"displayName": "Alma", "profileId": 101, "shortName": "AE"}],
                "recipients": [],
                "latestMessage": {"id": "m7", "sendDateTime": "2026-11-02T08:00:00+00:00", "text": {"html": "<p>Hej</p>"}},
            }
        ]

    async with set_up(hass, arrange) as (_, api):
        [entry] = hass.config_entries.async_entries(DOMAIN)
        state = hass.states.get("binary_sensor.aula_vacation_request_alma_school")
        assert state is not None and state.state == "on" and state.attributes["count"] == 1
        child = {"config_entry_id": entry.entry_id, "child_id": 11}
        with pytest.raises(ServiceValidationError) as off:
            await hass.services.async_call(DOMAIN, "report_sick", child, blocking=True, return_response=True)
        assert off.value.translation_key == "sick_reports_not_allowed" and "presence.updateStatusByInstitutionProfileIds" not in api.requests

        options: Any = hass.config_entries.options
        form = await options.async_init(entry.entry_id)
        assert (form["type"], form["step_id"]) == (FlowResultType.FORM, "init")
        assert {str(key): key.default() for key in form["data_schema"].schema} == {option: False for option in ALLOWED_CHANGES}
        saved = await options.async_configure(form["flow_id"], {option: True for option in ALLOWED_CHANGES})
        assert saved["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        [entry] = hass.config_entries.async_entries(DOMAIN)
        assert entry.state is ConfigEntryState.LOADED and entry.options == {option: True for option in ALLOWED_CHANGES}

        assert await hass.services.async_call(DOMAIN, "report_sick", child, blocking=True, return_response=True) == {"route": "aula"}
        assert api.presence_statuses == {11: 1}
        status = hass.states.get("sensor.aula_status_alma_school")
        assert status is not None and status.state == "1"
        await hass.services.async_call(DOMAIN, "undo_sick", child, blocking=True)
        assert api.presence_statuses == {11: 0}

        start, end = _next_monday(6), _next_monday(6) + timedelta(days=2)
        await hass.services.async_call(
            DOMAIN, "add_vacation", {"config_entry_id": entry.entry_id, "child_ids": [11], "start": start.isoformat(), "end": end.isoformat(), "comment": "Fri"}, blocking=True
        )
        [vacation] = api.vacations
        assert (vacation["startDate"], vacation["endDate"], vacation["description"]) == (start.isoformat(), end.isoformat(), "Fri")
        await hass.services.async_call(DOMAIN, "delete_vacation", {"config_entry_id": entry.entry_id, "vacation_id": vacation["vacationId"]}, blocking=True)
        assert api.vacations == []

        days = [
            {"date": monday.isoformat(), "is_coming": True, "entry_time": "08:00", "exit_time": "15:30"},
            {"date": (monday + timedelta(days=1)).isoformat(), "is_coming": False},
        ]
        await hass.services.async_call(DOMAIN, "answer_vacation_request", {**child, "request_id": 501, "days": days}, blocking=True)
        # Reading again after a write waits out the coordinator's cooldown; this test does not.
        await entry.runtime_data.data_coordinator.async_refresh()
        assert api.vacation_answers[501]["days"] == [
            {"date": monday.isoformat(), "isComing": True, "entryTime": "08:00", "exitTime": "15:30"},
            {"date": (monday + timedelta(days=1)).isoformat(), "isComing": False, "entryTime": "", "exitTime": ""},
        ]
        state = hass.states.get("binary_sensor.aula_vacation_request_alma_school")
        assert state is not None and state.state == "off"

        await hass.services.async_call(DOMAIN, "send_message", {**child, "thread_id": 7, "text": "Alma er syg"}, blocking=True)
        assert api.messages[7][-1]["text"] == {"html": "<p>Alma er syg</p>"}
        assert entry.runtime_data.memory.thread(11) is not None

        diagnostics: Any = await async_get_config_entry_diagnostics(hass, entry)
        assert "Alma er syg" not in json.dumps(diagnostics)
