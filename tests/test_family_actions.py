"""Tests of what a guardian does in Aula through the websocket API and the actions: report sick,
vacations, vacation requests and replies, and of the day today and what is remembered for it."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aula_connect.api.aula_errors import AulaCredentialError
from aula_connect.api.models.aula_message_thread_models import AulaThreadRecipient
from aula_connect.api.models.module import (
    AulaDailyOverview,
    AulaSickReportRoute,
    AulaVacation,
    AulaVacationDay,
    AulaVacationRequestDetail,
)
from aula_connect.tabulex import TabulexError
from aula_connect.widget_probe import WidgetProbeReport
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry, MockUser
from pytest_homeassistant.typing import MockHAClientWebSocket, WebSocketGenerator

from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.diagnostics import async_get_config_entry_diagnostics
from custom_components.aula_connect.family import FamilyMemory, previous_school_day, school_year_start

from .fake_aula import CHILD, PARENT, SCHOOL, FakeAula
from .test_calendars import ALL_ALLOWED, TZ, at, set_up
from .test_family_entities import message_thread, vacation_request
from .test_init import DATA


@pytest.fixture
async def client(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> MockHAClientWebSocket:
    """A websocket client, connected before a test moves the clock on."""
    assert await async_setup_component(hass, "calendar", {})
    return await hass_ws_client(hass)


def _entry_id(hass: HomeAssistant) -> str:
    return hass.config_entries.async_entries(DOMAIN)[0].entry_id


async def _ask(client: MockHAClientWebSocket, message: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    return await client.receive_json()


async def _result(client: MockHAClientWebSocket, message: dict[str, Any]) -> Any:
    answer = await _ask(client, message)
    assert answer["success"], answer
    return answer["result"]


async def _error(client: MockHAClientWebSocket, message: dict[str, Any]) -> dict[str, str]:
    answer = await _ask(client, message)
    assert not answer["success"], answer
    return answer["error"]


def _child(hass: HomeAssistant, command: str, **fields: Any) -> dict[str, Any]:
    return {"type": f"aula_connect/{command}", "entry_id": _entry_id(hass), "child_id": CHILD.id, **fields}


async def _today(client: MockHAClientWebSocket, hass: HomeAssistant) -> tuple[int, dict[str, Any]]:
    answer = await _ask(client, _child(hass, "today/subscribe"))
    assert answer["success"], answer
    message = await client.receive_json()
    assert (message["id"], message["type"]) == (answer["id"], "event")
    return answer["id"], message["event"]


async def test_the_day_today_tells_presence_sickness_and_requests_to_answer(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.daily_overviews = [
        AulaDailyOverview(
            id=1,
            institution_profile=SCHOOL,
            status=3,
            check_in_time=time(7, 55),
            check_in_time_expected=time(8, 0),
            check_out_time_expected=time(15, 30),
            exit_with="Mormor",
        )
    ]
    aula.vacation_requests = [vacation_request(1)]
    aula.routes = {CHILD.id: AulaSickReportRoute.AULA}
    await set_up(hass, freezer)
    _, today = await _today(client, hass)
    assert today == {
        "presence": {
            "status": 3,
            "check_in": "07:55",
            "check_out": None,
            "planned_in": "08:00",
            "planned_out": "15:30",
            "exit_with": "Mormor",
        },
        "sick": {"route": "aula", "today": False, "since": None, "wrote_yesterday": False},
        "vacation_requests_pending": 1,
    }


async def test_the_day_today_is_sent_again_at_each_read_until_the_entry_is_unloaded(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    msg_id, today = await _today(client, hass)
    assert today["presence"] is None and today["sick"]["route"] == "aula"

    aula.daily_overviews = [AulaDailyOverview(id=1, institution_profile=SCHOOL, status=1)]
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.data_coordinator
    await coordinator.async_refresh()
    message = await client.receive_json()
    assert message["id"] == msg_id
    assert message["event"]["sick"] == {"route": "aula", "today": True, "since": "2026-12-28", "wrote_yesterday": False}

    with patch.object(aula, "login", AsyncMock(side_effect=ConnectionError("down"))):
        await coordinator.async_refresh()

    assert await hass.config_entries.async_unload(_entry_id(hass))
    message = await client.receive_json()
    assert (message["id"], message["event"]) == (msg_id, {"ended": True})


async def test_the_day_today_ends_when_the_child_is_gone_and_is_refused_for_an_unknown_child(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    assert (await _error(client, _child(hass, "today/subscribe", child_id=99)))["code"] == "not_found"
    assert (await _error(client, {**_child(hass, "today/subscribe"), "entry_id": "nope"}))["code"] == "not_found"
    msg_id, _ = await _today(client, hass)
    aula.parent.children.clear()
    try:
        await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.data_coordinator.async_refresh()
        message = await client.receive_json()
        assert (message["id"], message["event"]) == (msg_id, {"ended": True})
    finally:
        aula.parent.children.append(CHILD)


async def test_reporting_sick_sends_it_once_remembers_the_day_and_reads_presence_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    reads = aula.reads.count("get_sick_report_route")
    assert await _result(client, _child(hass, "sick/report")) == {"route": "aula"}
    assert aula.writes == [("report_sick", (CHILD.id,))]
    assert aula.reads.count("get_sick_report_route") == reads
    _, today = await _today(client, hass)
    assert today["sick"]["today"] and today["sick"]["since"] == "2026-12-28"

    assert await _result(client, _child(hass, "sick/undo")) == {"route": "aula"}
    assert aula.writes[-1] == ("undo_sick", (CHILD.id,))
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert not entry.runtime_data.memory.was_sick(CHILD.id, date(2026, 12, 28))


@pytest.mark.parametrize(
    ("route", "code", "message"),
    [
        (AulaSickReportRoute.NONE, "not_supported", "Aula offers no way to report the child sick"),
    ],
)
async def test_a_child_reported_sick_another_way_is_not_reported(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket,
    route: AulaSickReportRoute, code: str, message: str,
) -> None:
    aula.routes = {CHILD.id: route}
    await set_up(hass, freezer)
    _, today = await _today(client, hass)
    assert today["sick"]["route"] == route.value
    assert await _error(client, _child(hass, "sick/report")) == {"code": code, "message": message}


async def test_a_write_aula_fails_says_it_may_have_been_done_without_the_token(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    aula.error = ConnectionError("url: /api?access_token=s3cr3t")
    error = await _error(client, _child(hass, "sick/report"))
    assert error == {
        "code": "home_assistant_error",
        "message": "Aula did not confirm it, and may or may not have done it: url: /api?access_token=REDACTED",
    }
    assert len(aula.writes) == 1
    error = await _error(client, _child(hass, "threads"))
    assert error["message"] == "Aula could not be read: url: /api?access_token=REDACTED"
    aula.error = ValueError("The vacation starts before today, 2026-12-28")
    error = await _error(client, {"type": "aula_connect/vacation/add", "entry_id": _entry_id(hass), "child_ids": [CHILD.id], "start": "2026-12-01", "end": "2026-12-02"})
    assert error == {"code": "invalid_format", "message": "Not sent: The vacation starts before today, 2026-12-28"}


async def test_a_write_aula_refuses_the_login_for_asks_to_log_in_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    aula.error = AulaCredentialError("expired")
    assert (await _error(client, _child(hass, "sick/report")))["code"] == "home_assistant_error"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == ["reauth"]


async def test_nothing_is_done_for_a_child_or_entry_not_there(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    for command in ("sick/report", "sick/undo", "threads", "vacations", "vacation_requests"):
        assert (await _error(client, _child(hass, command, child_id=99)))["code"] == "not_found"
    assert (await _error(client, {**_child(hass, "sick/report"), "entry_id": "nope"}))["code"] == "not_found"
    assert (await _error(client, _child(hass, "message/reply", thread_id=1, text="Hej", child_id=99)))["code"] == "not_found"
    assert (await _error(client, {"type": "aula_connect/vacation/delete", "entry_id": "nope", "vacation_id": 1}))["code"] == "not_found"
    assert aula.writes == []


def _employee(name: str) -> AulaThreadRecipient:
    return AulaThreadRecipient(name, name, "Lærer")


async def test_the_threads_about_a_child_come_newest_first_with_the_teachers_and_the_one_remembered(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.threads = [
        message_thread(1, "m1", recipients=[_employee("Jane Doe"), _employee(PARENT.name)]),
        message_thread(2, "m2", latest_message=None, started_datetime=at(28, 9, 30)),
        message_thread(3, "m3", is_thread_or_subscription_deleted=True),
        message_thread(4, "m4", regarding_children=[]),
    ]
    await set_up(hass, freezer)
    answer = await _result(client, _child(hass, "threads"))
    assert answer == {
        "threads": [
            {"thread_id": 2, "subject": "Thread 2", "last_activity": at(28, 9, 30).isoformat(), "employees": [], "can_reply": True},
            {"thread_id": 1, "subject": "Thread 1", "last_activity": at(28, 9).isoformat(), "employees": ["Jane Doe"], "can_reply": True},
            {"thread_id": 3, "subject": "Thread 3", "last_activity": at(28, 9).isoformat(), "employees": [], "can_reply": False},
        ],
        "remembered": None,
        "remembered_stale": False,
    }


async def test_a_reply_is_sent_in_a_thread_about_the_child_only_and_remembered(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.threads = [message_thread(1, "m1"), message_thread(3, "m3", is_thread_or_subscription_deleted=True), message_thread(4, "m4", regarding_children=[])]
    await set_up(hass, freezer)
    for thread_id in (3, 4, 5):
        error = await _error(client, _child(hass, "message/reply", thread_id=thread_id, text="Hej"))
        assert error["code"] == "not_found"
    assert aula.writes == []

    assert await _result(client, _child(hass, "message/reply", thread_id=1, text="Mille er syg")) == {}
    assert aula.writes == [("reply", (1, "Mille er syg"))]
    answer = await _result(client, _child(hass, "threads"))
    assert (answer["remembered"], answer["remembered_stale"]) == (1, False)

    freezer.move_to(at(29, 8))
    _, today = await _today(client, hass)
    assert today["sick"]["wrote_yesterday"] is True


@pytest.mark.parametrize(
    "changed",
    [
        {"regarding_children": []},
        {"started_datetime": datetime(2026, 7, 31, 12, tzinfo=TZ)},
        {"is_thread_or_subscription_deleted": True},
    ],
)
async def test_a_remembered_thread_gone_from_before_the_school_year_or_closed_is_asked_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket, changed: dict[str, Any]
) -> None:
    aula.threads = [message_thread(1, "m1")]
    await set_up(hass, freezer)
    await _result(client, _child(hass, "message/reply", thread_id=1, text="Hej"))
    aula.threads = [message_thread(1, "m1", **changed)]
    answer = await _result(client, _child(hass, "threads"))
    assert (answer["remembered"], answer["remembered_stale"]) == (1, True)


async def test_vacations_are_listed_added_and_deleted(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.vacations = [
        AulaVacation(7, CHILD.id, date(2027, 7, 5), date(2027, 7, 23), "Sommerferie", True),
        AulaVacation(8, 99, date(2027, 7, 5), date(2027, 7, 23), None, False),
    ]
    await set_up(hass, freezer)
    assert await _result(client, _child(hass, "vacations")) == {
        "vacations": [{"id": 7, "start": "2027-07-05", "end": "2027-07-23", "comment": "Sommerferie", "editable": True}]
    }
    add = {"type": "aula_connect/vacation/add", "entry_id": _entry_id(hass), "child_ids": [CHILD.id], "start": "2027-07-05", "end": "2027-07-23"}
    assert await _result(client, add) == {}
    assert await _result(client, {**add, "comment": "Fri"}) == {}
    assert (await _error(client, {**add, "child_ids": [99]}))["code"] == "not_found"
    assert await _result(client, {"type": "aula_connect/vacation/delete", "entry_id": _entry_id(hass), "vacation_id": 7}) == {}
    assert aula.writes == [
        ("add_vacation", ([CHILD.id], date(2027, 7, 5), date(2027, 7, 23), "")),
        ("add_vacation", ([CHILD.id], date(2027, 7, 5), date(2027, 7, 23), "Fri")),
        ("delete_vacation", (7,)),
    ]


async def test_vacation_requests_list_every_weekday_as_answered_and_are_answered_day_by_day(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.vacation_requests = [
        vacation_request(1, start_date=date(2027, 2, 12), end_date=date(2027, 2, 16), is_presence_times_required=None),
        vacation_request(2),
        vacation_request(3, child_id=99),
    ]
    aula.vacation_request_details = {
        1: AulaVacationRequestDetail(
            1, CHILD.id, date(2027, 2, 12), date(2027, 2, 16), date(2027, 1, 10), True, False, None,
            [AulaVacationDay(date(2027, 2, 15), True, time(8), time(15, 30)), AulaVacationDay(date(2027, 2, 16), False)],
        ),
        3: AulaVacationRequestDetail(3, 99, date(2027, 2, 15), date(2027, 2, 19), None, False, True, None, []),
    }
    await set_up(hass, freezer)
    answer = await _result(client, _child(hass, "vacation_requests"))
    first, second = answer["requests"]
    assert first == {
        "id": 1,
        "title": "Juleferie",
        "start": "2027-02-12",
        "end": "2027-02-16",
        "deadline": "2027-01-10",
        "missing_answer": True,
        "note": "Svar venligst",
        "times_required": True,
        "days": [
            {"date": "2027-02-12", "is_coming": None, "entry_time": None, "exit_time": None},
            {"date": "2027-02-15", "is_coming": True, "entry_time": "08:00", "exit_time": "15:30"},
            {"date": "2027-02-16", "is_coming": False, "entry_time": None, "exit_time": None},
        ],
    }
    assert (second["id"], second["times_required"], len(second["days"])) == (2, False, 5)

    days = [
        {"date": "2027-02-12", "is_coming": False},
        {"date": "2027-02-15", "is_coming": True, "entry_time": "08:00", "exit_time": "15:30"},
    ]
    assert await _result(client, _child(hass, "vacation_request/answer", request_id=1, days=days, comment="Tak")) == {}
    assert aula.writes == [
        ("answer_vacation_request", (1, [AulaVacationDay(date(2027, 2, 12), False), AulaVacationDay(date(2027, 2, 15), True, time(8), time(15, 30))], "Tak"))
    ]
    for other in (2, 3):
        error = await _error(client, _child(hass, "vacation_request/answer", request_id=other, days=days))
        assert error["code"] == "not_found"
    assert len(aula.writes) == 1


async def _entry(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> str:
    await set_up(hass, freezer)
    return _entry_id(hass)


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any], context: Context | None = None) -> Any:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, context=context, return_response=service in ("probe_widgets",)
    )


async def test_the_actions_do_what_the_cards_do(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, hass_read_only_user: MockUser
) -> None:
    aula.threads = [message_thread(1, "m1")]
    aula.vacation_request_details = {
        1: AulaVacationRequestDetail(1, CHILD.id, date(2027, 2, 15), date(2027, 2, 19), None, False, True, None, [])
    }
    entry_id = await _entry(hass, freezer)
    user = Context(user_id=hass_read_only_user.id)
    child = {"config_entry_id": entry_id, "child_id": CHILD.id}
    response = await hass.services.async_call(DOMAIN, "report_sick", child, blocking=True, context=user, return_response=True)
    assert response == {"route": "aula"}
    await _call(hass, "undo_sick", child, user)
    await _call(hass, "add_vacation", {"config_entry_id": entry_id, "child_ids": CHILD.id, "start": "2027-07-05", "end": "2027-07-06"}, user)
    await _call(hass, "delete_vacation", {"config_entry_id": entry_id, "vacation_id": 7}, user)
    await _call(hass, "answer_vacation_request", {**child, "request_id": 1, "days": [{"date": "2027-02-15", "is_coming": False}]}, user)
    await _call(hass, "send_message", {**child, "thread_id": 1, "text": "Hej"}, user)
    assert [method for method, _ in aula.writes] == [
        "report_sick", "undo_sick", "add_vacation", "delete_vacation", "answer_vacation_request", "reply"
    ]
    assert aula.writes[2] == ("add_vacation", ([CHILD.id], date(2027, 7, 5), date(2027, 7, 6), ""))


async def test_an_action_that_cannot_be_done_says_why_in_the_users_language(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    entry_id = await _entry(hass, freezer)
    child = {"config_entry_id": entry_id, "child_id": CHILD.id}
    with pytest.raises(ServiceValidationError) as missing:
        await _call(hass, "report_sick", {**child, "child_id": 99})
    assert (missing.value.translation_key, missing.value.translation_placeholders) == ("child_not_found", {"child_id": "99"})
    with pytest.raises(ServiceValidationError) as unloaded:
        await _call(hass, "report_sick", {**child, "config_entry_id": "nope"})
    assert unloaded.value.translation_key == "entry_not_loaded"
    with pytest.raises(ServiceValidationError) as thread:
        await _call(hass, "send_message", {**child, "thread_id": 5, "text": "Hej"})
    assert thread.value.translation_key == "thread_not_found"
    aula.routes = {CHILD.id: AulaSickReportRoute.TABULEX}
    assert await hass.services.async_call(DOMAIN, "report_sick", child, blocking=True, return_response=True) == {"route": "tabulex"}
    with pytest.raises(ServiceValidationError) as final:
        await _call(hass, "undo_sick", child)
    assert (final.value.translation_key, final.value.translation_placeholders) == ("sick_report_final", {})
    for write, key in ((True, "sick_report_unconfirmed"), (False, "sick_report_unprepared")):
        aula.error = TabulexError("Tabulex answered HTTP 500 for the children")
        with pytest.raises(HomeAssistantError) as tabulex:
            await _call(hass, "report_sick" if write else "probe_widgets", child if write else {"config_entry_id": entry_id})
        assert (tabulex.value.translation_key, tabulex.value.translation_placeholders) == (key, {})
    aula.error = None
    aula.routes = {}
    aula.error = ConnectionError("down")
    with pytest.raises(HomeAssistantError) as failed:
        await _call(hass, "report_sick", child)
    assert not isinstance(failed.value, ServiceValidationError)
    assert (failed.value.translation_key, failed.value.translation_placeholders) == ("write_failed", {"error": "down"})


def _report() -> WidgetProbeReport:
    return WidgetProbeReport(widgets=())


async def test_only_an_administrator_may_probe_the_widgets_whose_report_is_in_the_diagnostics(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, hass_admin_user: MockUser, hass_read_only_user: MockUser
) -> None:
    entry_id = await _entry(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    with patch.object(entry.runtime_data.client, "read_everything", AsyncMock(return_value=[]), create=True), patch.object(
        entry.runtime_data.client, "responses", MagicMock(return_value={}), create=True
    ):
        assert (await async_get_config_entry_diagnostics(hass, entry))["widget_probe"] is None
        aula.probe = _report()
        with pytest.raises(Unauthorized):
            await _call(hass, "probe_widgets", {"config_entry_id": entry_id}, Context(user_id=hass_read_only_user.id))
        answer = await _call(hass, "probe_widgets", {"config_entry_id": entry_id}, Context(user_id=hass_admin_user.id))
        assert (answer, aula.probe_reads) == (_report().as_dict(), ())
        reads = {"config_entry_id": entry_id, "reads": ["/api/children", "https://foraeldre.tabulex.net/api/fravaer"]}
        await _call(hass, "probe_widgets", reads, Context(user_id=hass_admin_user.id))
        assert aula.probe_reads == ("/api/children", "https://foraeldre.tabulex.net/api/fravaer")
        assert (await async_get_config_entry_diagnostics(hass, entry))["widget_probe"] == _report().as_dict()


def test_the_school_day_before_monday_is_friday_and_the_school_year_starts_in_august() -> None:
    assert previous_school_day(date(2026, 12, 28)) == date(2026, 12, 25)
    assert previous_school_day(date(2026, 12, 29)) == date(2026, 12, 28)
    assert school_year_start(date(2026, 12, 28)) == date(2026, 8, 1)
    assert school_year_start(date(2027, 3, 1)) == date(2026, 8, 1)
    assert school_year_start(date(2026, 8, 1)) == date(2026, 8, 1)


async def test_a_sickness_goes_back_over_the_weekend_and_old_days_are_forgotten(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    freezer.move_to(at(28, 10))
    memory = FamilyMemory(hass, "entry")
    await memory.async_load()
    for day in (date(2026, 9, 1), date(2026, 12, 23), date(2026, 12, 24), date(2026, 12, 25)):
        memory.note_sick(CHILD.id, day)
    memory.note_sick(CHILD.id, date(2026, 12, 25))
    assert memory.sick_since(CHILD.id, date(2026, 12, 28), sick_today=True) == date(2026, 12, 23)
    assert memory.sick_since(CHILD.id, date(2026, 12, 28), sick_today=False) == date(2026, 12, 23)
    assert memory.sick_since(CHILD.id, date(2026, 12, 30), sick_today=False) is None
    memory.forget_sick(CHILD.id, date(2026, 12, 1))
    memory.tick(CHILD.id, "1", date(2026, 9, 1), True)
    memory.note_wrote(CHILD.id, 5, at(28, 9))
    memory.note_wrote(CHILD.id, 5, at(28, 10))
    await memory.async_save()

    later = FamilyMemory(hass, "entry")
    await later.async_load()
    assert later.was_sick(CHILD.id, date(2026, 12, 23)) and not later.was_sick(CHILD.id, date(2026, 9, 1))
    assert later.ticked(CHILD.id) == set()
    assert later.thread(CHILD.id) == {"id": 5, "at": at(28, 10).isoformat()}
    await later.async_remove()
    assert "aula_connect.family.entry" not in hass_storage


async def test_a_child_seen_sick_in_the_presence_read_is_remembered_and_its_route_read_hourly(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.daily_overviews = [
        AulaDailyOverview(id=1, institution_profile=SCHOOL, status=1),
        AulaDailyOverview(id=2, institution_profile=replace(SCHOOL, id=99), status=1),
    ]
    await set_up(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    memory = entry.runtime_data.memory
    assert memory.was_sick(CHILD.id, date(2026, 12, 28)) and not memory.was_sick(99, date(2026, 12, 28))
    routes = aula.reads.count("get_sick_report_route")
    coordinator = entry.runtime_data.data_coordinator
    await coordinator.async_refresh()
    assert aula.reads.count("get_sick_report_route") == routes
    freezer.tick(timedelta(hours=1))
    with patch.object(aula, "get_sick_report_route", AsyncMock(side_effect=ConnectionError("down"))):
        await coordinator.async_refresh()
    assert "sick report routes" in coordinator.data.unreadable
    assert coordinator.data.sick_routes == {CHILD.id: AulaSickReportRoute.AULA}
    await coordinator.async_refresh()
    assert coordinator.data.unreadable == frozenset()
    with patch.object(aula, "get_sick_report_route", AsyncMock(side_effect=AulaCredentialError("expired"))):
        freezer.tick(timedelta(hours=1))
        await coordinator.async_refresh()
    assert entry.state is ConfigEntryState.LOADED and not coordinator.last_update_success


async def test_a_second_entry_of_the_same_family_counts_its_guardian_as_family(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.threads = [message_thread(1, "m1", recipients=[_employee("Jane Doe"), _employee(PARENT.name)])]
    await set_up(hass, freezer)
    second = MockConfigEntry(domain=DOMAIN, unique_id="other", title="Aula 2", data={**DATA, "entity_prefix": "Aula 2"}, version=2, minor_version=2)
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    answer = await _result(client, _child(hass, "threads"))
    assert answer["threads"][0]["employees"] == ["Jane Doe"]
    assert await hass.config_entries.async_unload(second.entry_id)


@pytest.mark.parametrize(
    ("option", "service", "command", "key"),
    [
        ("allow_sick_reports", "report_sick", {"type": "aula_connect/sick/report"}, "sick_reports_not_allowed"),
        ("allow_sick_reports", "undo_sick", {"type": "aula_connect/sick/undo"}, "sick_reports_not_allowed"),
        ("allow_messages", "send_message", {"type": "aula_connect/message/reply", "thread_id": 1, "text": "Hej"}, "messages_not_allowed"),
    ],
)
async def test_a_change_the_options_do_not_allow_is_refused_by_the_action_and_the_card_alike(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket,
    option: str, service: str, command: dict[str, Any], key: str,
) -> None:
    aula.threads = [message_thread(1, "m1")]
    await set_up(hass, freezer, options={**ALL_ALLOWED, option: False})
    child = {"config_entry_id": _entry_id(hass), "child_id": CHILD.id}
    data = {**child, "thread_id": 1, "text": "Hej"} if service == "send_message" else child
    with pytest.raises(ServiceValidationError) as refused:
        await _call(hass, service, data)
    assert refused.value.translation_key == key
    await client.send_json_auto_id({**command, "entry_id": _entry_id(hass), "child_id": CHILD.id})
    answer = await client.receive_json()
    assert (answer["success"], answer["error"]["code"]) == (False, "not_allowed")
    assert aula.writes == []


async def test_vacations_and_answers_to_requests_the_options_do_not_allow_are_refused(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer, options={})
    entry_id = _entry_id(hass)
    calls: list[tuple[str, dict[str, Any], str]] = [
        ("add_vacation", {"config_entry_id": entry_id, "child_ids": [CHILD.id], "start": "2027-02-15", "end": "2027-02-16"}, "vacations_not_allowed"),
        ("delete_vacation", {"config_entry_id": entry_id, "vacation_id": 5}, "vacations_not_allowed"),
        ("answer_vacation_request", {"config_entry_id": entry_id, "child_id": CHILD.id, "request_id": 1, "days": list[Any]()}, "vacation_requests_not_allowed"),
    ]
    for service, data, key in calls:
        with pytest.raises(ServiceValidationError) as refused:
            await _call(hass, service, data)
        assert refused.value.translation_key == key
    assert aula.writes == []


@pytest.mark.parametrize(
    ("route", "found", "sends"),
    [(AulaSickReportRoute.TABULEX, True, "PUT /x"), (AulaSickReportRoute.NONE, False, None)],
)
async def test_checking_a_sick_report_sends_nothing_whatever_the_options_allow(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket,
    route: AulaSickReportRoute, found: bool, sends: str | None,
) -> None:
    aula.routes = {CHILD.id: route}
    await set_up(hass, freezer, options={})
    child = {"config_entry_id": _entry_id(hass), "child_id": CHILD.id}
    answer = await hass.services.async_call(DOMAIN, "check_sick_report", child, blocking=True, return_response=True)
    assert answer == {"route": route.value, "child_found": found, "same_institution": found or None, "sick_today": False, "would_send": sends}
    assert aula.writes == []
    await client.send_json_auto_id({"type": "aula_connect/sick/check", "entry_id": _entry_id(hass), "child_id": CHILD.id})
    assert (await client.receive_json())["result"] == answer


async def test_the_diagnostics_tell_what_reporting_each_child_sick_would_do_as_yes_and_no(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.routes = {CHILD.id: AulaSickReportRoute.TABULEX}
    entry_id = await _entry(hass, freezer)
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    with patch.object(entry.runtime_data.client, "read_everything", AsyncMock(return_value=[]), create=True), patch.object(
        entry.runtime_data.client, "responses", MagicMock(return_value={}), create=True
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["sick_report_checks"] == [{"route": "tabulex", "child_found": True, "same_institution": True, "sick_today": False}]
        aula.error = TabulexError("Tabulex answered HTTP 500 for the children")
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["sick_report_checks"] == [{"error": "sick_report_unprepared"}]
