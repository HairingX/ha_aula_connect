"""Tests of the websocket API that shows a child's days: lessons, events, weekly plan tasks and birthdays."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from unittest.mock import patch

import pytest
from aula_connect.api.aula_errors import AulaCredentialError
from aula_connect.api.models.module import (
    AulaBirthdayEvent,
    AulaCalendarEvent,
    AulaCalendarEventLesson,
    AulaCalendarEventLessonParticipant,
    AulaCalendarEventResource,
    AulaCalendarEventTimeSlot,
    AulaCalendarEventTimeSlotEntry,
    AulaCalendarEventTimeSlotEntryAnswer,
    AulaCalendarEventTimeSlotEntryIndex,
    AulaDailyPlan,
    AulaDailyPlanTask,
    AulaWeeklyPlan,
)
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import MockHAClientWebSocket, WebSocketGenerator

from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.websocket_api import all_day_dates

from .fake_aula import CHILD, PARENT, SCHOOL, FakeAula
from .test_calendars import at, event, set_up
from .test_init import DATA


@pytest.fixture
async def client(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> MockHAClientWebSocket:
    """A websocket client, connected before a test moves the clock on past its access token's expiry."""
    # The calendars register their HTTP view, which the running server would no longer take.
    assert await async_setup_component(hass, "calendar", {})
    return await hass_ws_client(hass)


def _entry_id(hass: HomeAssistant) -> str:
    return hass.config_entries.async_entries(DOMAIN)[0].entry_id


def _lesson(event_id: int, title: str, start: datetime, end: datetime, **fields: Any) -> AulaCalendarEvent:
    return event(event_id, title, start, end, type="lesson", **fields)


def _task(task_id: int, content: str, **fields: Any) -> AulaDailyPlanTask:
    values: dict[str, Any] = {"author": "JD", "content": content, "group": "3A", "id": task_id, "pill": "Dansk", "type": "task"}
    values.update(fields)
    return AulaDailyPlanTask(**values)


def _plan(*days: AulaDailyPlan) -> AulaWeeklyPlan:
    return AulaWeeklyPlan(date(2026, 12, 28), "Week 53", date(2027, 1, 3), "alma", list(days))


def _meeting(booked_by: int | None) -> AulaCalendarEvent:
    """A meeting over two days, with the second of its two times booked by `booked_by`."""
    times = [
        AulaCalendarEventTimeSlotEntryIndex(start_datetime=at(29, 14), end_datetime=at(29, 14, 15)),
        AulaCalendarEventTimeSlotEntryIndex(start_datetime=at(29, 14, 15), end_datetime=at(29, 14, 30)),
    ]
    answers = [] if booked_by is None else [AulaCalendarEventTimeSlotEntryAnswer(booked_by, 1, booked_by, 1)]
    slot = AulaCalendarEventTimeSlotEntry(answers, at(30, 16), 1, at(29, 8), times)
    return event(
        3,
        "Meeting",
        at(29, 8),
        at(30, 16),
        type="school_home_meeting",
        belongs_to_profiles=[CHILD.id],
        time_slot=AulaCalendarEventTimeSlot([slot]),
    )


def _birthday(name: str, born: date) -> AulaBirthdayEvent:
    return AulaBirthdayEvent(
        institution_profile_id=31, birthday_date=born, institution_code="S1", full_name=name, main_group_name="3A"
    )


async def _subscribe(
    client: MockHAClientWebSocket, entry_id: str, start: str, end: str, child_id: int = CHILD.id
) -> dict[str, Any]:
    """Subscribe to the days, and the first event the subscription sends."""
    await client.send_json_auto_id(
        {"type": "aula_connect/week/subscribe", "entry_id": entry_id, "child_id": child_id, "start": start, "end": end}
    )
    result = await client.receive_json()
    assert result["success"], result
    message = await client.receive_json()
    assert (message["id"], message["type"]) == (result["id"], "event")
    return message["event"]


async def _error(client: MockHAClientWebSocket, message: dict[str, Any]) -> str:
    await client.send_json_auto_id(message)
    result = await client.receive_json()
    assert not result["success"]
    return result["error"]["code"]


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, moment: datetime) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_the_children_of_every_loaded_entry_are_listed(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    second = MockConfigEntry(
        domain=DOMAIN, unique_id="other", title="Aula 2", data={**DATA, "entity_prefix": "Aula 2"}, version=2, minor_version=2
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    await client.send_json_auto_id({"type": "aula_connect/children"})
    result = await client.receive_json()
    alma = {"child_id": 11, "first_name": "Alma", "name": "Alma Example", "institution_name": "School"}
    assert result["result"] == {
        "children": [{"entry_id": _entry_id(hass), **alma}, {"entry_id": second.entry_id, **alma}]
    }

    assert await hass.config_entries.async_unload(second.entry_id)
    await client.send_json_auto_id({"type": "aula_connect/children"})
    result = await client.receive_json()
    assert result["result"] == {"children": [{"entry_id": _entry_id(hass), **alma}]}


async def test_the_days_are_the_lessons_events_tasks_and_birthdays_aula_was_read_for(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    teacher = AulaCalendarEventLessonParticipant("substituteTeacher", 1, "JD", "Jane Doe")
    nameless = AulaCalendarEventLessonParticipant("primaryTeacher", 2, "", "")
    aula.events[SCHOOL.id] = [
        _lesson(
            1,
            "Math",
            at(29, 9),
            at(29, 10),
            lesson=AulaCalendarEventLesson("L1", "substitute", [teacher, nameless]),
            primary_resource=AulaCalendarEventResource(5, "Room 4"),
        ),
        _lesson(2, "Art", at(28, 8), at(28, 9)),
        event(4, "Trip", at(30, 0), at(31, 0), all_day=True, primary_resource=AulaCalendarEventResource(6, "Zoo")),
        _meeting(SCHOOL.id),
        event(5, "Parents' evening", at(29, 19), at(29, 21), type="parental_meeting"),
        event(6, "Later", at(20, 9, month=1, year=2027), at(20, 10, month=1, year=2027)),
    ]
    aula.weekly_plans = [
        _plan(
            AulaDailyPlan(date(2026, 12, 29), [_task(41, "<p>Pages 4-5</p>", title="Homework")]),
            AulaDailyPlan(date(2026, 12, 28), [_task(42, "Bring boots", type="comment", pill="", author="")]),
            AulaDailyPlan(date(2027, 1, 20), [_task(43, "Later")]),
        )
    ]
    aula.birthdays = [_birthday("Bo Friend", date(2017, 1, 3)), _birthday("Cy Friend", date(2017, 6, 1))]
    await set_up(hass, freezer)
    # What Aula was read for is shown as it was read.
    aula.events = {}
    aula.weekly_plans = []
    aula.birthdays = []

    days = await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")

    assert days == {
        "start": "2026-12-28",
        "end": "2027-01-11",
        "week_plan": True,
        "lessons": [
            {
                "id": "2",
                "start": "2026-12-28T08:00:00+01:00",
                "end": "2026-12-28T09:00:00+01:00",
                "title": "Art",
                "teachers": [],
                "location": None,
                "status": None,
            },
            {
                "id": "1",
                "start": "2026-12-29T09:00:00+01:00",
                "end": "2026-12-29T10:00:00+01:00",
                "title": "Math",
                "teachers": ["Jane Doe"],
                "location": "Room 4",
                "status": "substitute",
            },
        ],
        "events": [
            {
                "id": "3",
                "start": "2026-12-29T14:15:00+01:00",
                "end": "2026-12-29T14:30:00+01:00",
                "all_day": False,
                "title": "Meeting",
                "type": "school_home_meeting",
                "location": "School",
            },
            {
                "id": "4",
                "start": "2026-12-30",
                "end": "2026-12-31",
                "all_day": True,
                "title": "Trip",
                "type": "event",
                "location": "Zoo",
            },
        ],
        "tasks": [
            {
                "id": "42",
                "date": "2026-12-28",
                "type": "comment",
                "title": None,
                "content": "Bring boots",
                "subject": "",
                "group": "3A",
                "author": "",
            },
            {
                "id": "41",
                "date": "2026-12-29",
                "type": "task",
                "title": "Homework",
                "content": "<p>Pages 4-5</p>",
                "subject": "Dansk",
                "group": "3A",
                "author": "JD",
            },
        ],
        "birthdays": [{"date": "2027-01-03", "name": "Bo Friend"}],
    }


async def test_a_meeting_not_yet_booked_or_without_times_to_book_spans_its_days(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.events[SCHOOL.id] = [
        _meeting(None),
        event(4, "Talk", at(31, 8), at(31, 9), type="parental_meeting", belongs_to_profiles=[CHILD.id]),
    ]
    await set_up(hass, freezer)

    days = await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-04")

    assert [(item["title"], item["start"], item["end"], item["location"]) for item in days["events"]] == [
        ("Meeting", "2026-12-29T08:00:00+01:00", "2026-12-30T16:00:00+01:00", None),
        ("Talk", "2026-12-31T08:00:00+01:00", "2026-12-31T09:00:00+01:00", None),
    ]


async def test_days_aula_was_not_read_for_are_read_now(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    across = event(7, "Camp", at(27, 12), at(28, 12))
    aula.events[SCHOOL.id] = [across, _lesson(1, "Math", at(29, 9), at(29, 10))]
    aula.weekly_plans = [_plan(AulaDailyPlan(date(2026, 12, 29), [_task(41, "Read")]))]
    await set_up(hass, freezer)
    aula.events[SCHOOL.id] = [
        across,
        _lesson(8, "Music", at(22, 9), at(22, 10)),
        _lesson(9, "Not read yet", at(30, 9), at(30, 10)),
    ]
    aula.weekly_plans = [
        AulaWeeklyPlan(date(2026, 12, 21), "Week 52", date(2026, 12, 27), "alma", [
            AulaDailyPlan(date(2026, 12, 22), [_task(44, "Sing")]),
        ]),
        _plan(AulaDailyPlan(date(2026, 12, 29), [_task(45, "Not read yet")])),
    ]
    aula.birthdays = [_birthday("Di Friend", date(2018, 12, 23))]

    days = await _subscribe(client, _entry_id(hass), "2026-12-21", "2027-01-04")

    assert [(lesson["id"], lesson["title"]) for lesson in days["lessons"]] == [("8", "Music"), ("1", "Math")]
    assert [item["title"] for item in days["events"]] == ["Camp"]
    assert [task["content"] for task in days["tasks"]] == ["Sing", "Read"]
    assert days["birthdays"] == [{"date": "2026-12-23", "name": "Di Friend"}]


async def test_a_birthday_on_the_29th_of_february_is_on_the_28th_outside_a_leap_year(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    aula.birthdays = [_birthday("Bo Friend", date(2016, 2, 29))]

    days = await _subscribe(client, _entry_id(hass), "2027-02-22", "2027-03-08")

    assert days["birthdays"] == [{"date": "2027-02-28", "name": "Bo Friend"}]


async def test_an_institution_without_weekly_plans_has_no_tasks_or_birthdays(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.widgets = set()
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10))]
    aula.weekly_plans = [_plan(AulaDailyPlan(date(2026, 12, 29), [_task(41, "Read")]))]
    aula.birthdays = [_birthday("Bo Friend", date(2017, 1, 3))]
    await set_up(hass, freezer)

    days = await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")

    assert [lesson["title"] for lesson in days["lessons"]] == ["Math"]
    assert (days["week_plan"], days["tasks"], days["birthdays"]) == (False, [], [])


async def test_days_aula_cannot_be_read_for_are_left_empty_and_said_so_once(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    client: MockHAClientWebSocket,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10))]
    down = ConnectionError("down")
    with (
        patch.object(aula, "get_calendar_events", side_effect=down),
        patch.object(aula, "get_weekly_plans", side_effect=down),
        patch.object(aula, "get_birthday_events", side_effect=down),
    ):
        await set_up(hass, freezer)
        days = await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")

    assert (days["lessons"], days["events"], days["tasks"], days["birthdays"]) == ([], [], [], [])
    for what in ("events", "weekly plans", "birthdays"):
        assert caplog.text.count(f"Aula could not read the {what} for the period: down") == 1


async def test_days_whose_login_aula_no_longer_accepts_ask_to_log_in_again_and_show_what_was_read(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10))]
    await set_up(hass, freezer)

    with patch.object(aula, "get_calendar_events", side_effect=AulaCredentialError("expired")):
        days = await _subscribe(client, _entry_id(hass), "2026-12-21", "2027-01-04")
    await hass.async_block_till_done()

    assert [lesson["title"] for lesson in days["lessons"]] == ["Math"]
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == ["reauth"]


async def test_the_days_are_sent_again_when_aula_is_read_anew_for_the_child(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10))]
    await set_up(hass, freezer)
    days = await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")
    assert [lesson["title"] for lesson in days["lessons"]] == ["Math"]
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10)), _lesson(2, "Gym", at(30, 9), at(30, 10))]

    # Neither a refresh with nothing due nor one that fails sends anything.
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.calendar_coordinator
    await coordinator.async_refresh()
    with patch.object(aula, "login", side_effect=ConnectionError("down")):
        await coordinator.async_refresh()
    await _poll(hass, freezer, at(29, 10, 1))

    message = await client.receive_json()
    assert message["type"] == "event"
    assert [lesson["title"] for lesson in message["event"]["lessons"]] == ["Math", "Gym"]


async def test_unsubscribing_stops_the_days_being_sent(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")
    subscription = 1

    await client.send_json_auto_id({"type": "unsubscribe_events", "subscription": subscription})
    assert (await client.receive_json())["success"]
    await _poll(hass, freezer, at(29, 10, 1))

    await client.send_json_auto_id({"type": "aula_connect/children"})
    message = await client.receive_json()
    assert (message["type"], message["id"]) == ("result", 3)


async def test_a_user_who_is_not_an_administrator_can_read_the_days(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    assert await async_setup_component(hass, "calendar", {})
    # Connected before the clock is moved on past the access token's expiry.
    client = await hass_ws_client(hass, hass_read_only_access_token)
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10))]
    await set_up(hass, freezer)

    await client.send_json_auto_id({"type": "aula_connect/children"})
    assert len((await client.receive_json())["result"]["children"]) == 1
    days = await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")
    assert [lesson["title"] for lesson in days["lessons"]] == ["Math"]


async def test_no_token_mitid_user_id_or_unilogin_is_sent(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    child = replace(CHILD, user_id="unilogin-secret")
    aula.parent = replace(PARENT, children=[child], user_id="parent-unilogin-secret")
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10))]
    aula.weekly_plans = [
        AulaWeeklyPlan(date(2026, 12, 28), "Week 53", date(2027, 1, 3), "unilogin-secret", [
            AulaDailyPlan(date(2026, 12, 29), [_task(41, "Read")]),
        ])
    ]
    aula.birthdays = [_birthday("Bo Friend", date(2017, 1, 3))]
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    freezer.move_to(at(28, 10))
    secrets = {
        "mitid_username": "mitid-user-secret",
        "access_token": "access-token-secret",
        "refresh_token": "refresh-token-secret",
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="account",
        title="Aula",
        data={**DATA, **secrets, "entity_prefix": "Aula"},
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await client.send_json_auto_id({"type": "aula_connect/children"})
    children = await client.receive_json()
    days = await _subscribe(client, entry.entry_id, "2026-12-28", "2027-01-11")

    assert days["lessons"] and days["tasks"] and days["birthdays"]
    sent = json.dumps([children, days])
    assert "secret" not in sent


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-12-28", "2026-12-28"),
        ("2026-12-28", "2026-12-27"),
        ("2026-12-28", "2027-01-19"),
        ("2026-12-28", "28-12-2026"),
        ("2026-12-28", 20261228),
    ],
)
async def test_a_period_that_is_not_one_to_21_days_is_refused(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    client: MockHAClientWebSocket,
    start: str,
    end: Any,
) -> None:
    await set_up(hass, freezer)
    message = {"type": "aula_connect/week/subscribe", "entry_id": _entry_id(hass), "child_id": CHILD.id}
    assert await _error(client, {**message, "start": start, "end": end}) == "invalid_format"


async def test_a_period_of_21_days_is_accepted(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    days = await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-18")
    assert (days["start"], days["end"]) == ("2026-12-28", "2027-01-18")


async def test_a_subscription_without_a_child_is_refused(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    message = {"type": "aula_connect/week/subscribe", "entry_id": _entry_id(hass), "start": "2026-12-28", "end": "2027-01-04"}
    assert await _error(client, message) == "invalid_format"


@pytest.mark.parametrize(("entry_id", "child_id"), [("no such entry", CHILD.id), (None, 99)])
async def test_an_unknown_entry_or_child_is_not_found(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    client: MockHAClientWebSocket,
    entry_id: str | None,
    child_id: int,
) -> None:
    await set_up(hass, freezer)
    message = {
        "type": "aula_connect/week/subscribe",
        "entry_id": entry_id or _entry_id(hass),
        "child_id": child_id,
        "start": "2026-12-28",
        "end": "2027-01-04",
    }
    assert await _error(client, message) == "not_found"


async def test_an_unloaded_entry_is_not_found(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    entry_id = _entry_id(hass)
    assert await hass.config_entries.async_unload(entry_id)
    message = {
        "type": "aula_connect/week/subscribe",
        "entry_id": entry_id,
        "child_id": CHILD.id,
        "start": "2026-12-28",
        "end": "2027-01-04",
    }
    assert await _error(client, message) == "not_found"


async def test_unloading_the_entry_ends_the_subscription_once(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")

    assert await hass.config_entries.async_unload(_entry_id(hass))
    await hass.async_block_till_done()

    assert await client.receive_json() == {"id": 1, "type": "event", "event": {"ended": True}}
    await client.send_json_auto_id({"type": "unsubscribe_events", "subscription": 1})
    assert (await client.receive_json())["success"]


async def test_reloading_the_entry_ends_the_subscription_and_a_new_one_follows_the_reloaded_entry(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10))]
    await set_up(hass, freezer)
    await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")

    assert await hass.config_entries.async_reload(_entry_id(hass))
    await hass.async_block_till_done()

    assert await client.receive_json() == {"id": 1, "type": "event", "event": {"ended": True}}
    days = await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")
    assert [lesson["title"] for lesson in days["lessons"]] == ["Math"]
    aula.events[SCHOOL.id] = [_lesson(1, "Math", at(29, 9), at(29, 10)), _lesson(2, "Gym", at(30, 9), at(30, 10))]
    await _poll(hass, freezer, at(29, 10, 1))
    message = await client.receive_json()
    assert message["id"] == 2
    assert [lesson["title"] for lesson in message["event"]["lessons"]] == ["Math", "Gym"]


async def test_a_child_gone_from_aula_ends_its_subscription(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, client: MockHAClientWebSocket
) -> None:
    await set_up(hass, freezer)
    await _subscribe(client, _entry_id(hass), "2026-12-28", "2027-01-11")
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.data_coordinator

    # A refresh that fails says nothing of the children.
    with patch.object(aula, "login", side_effect=ConnectionError("down")):
        await coordinator.async_refresh()
    await coordinator.async_refresh()
    aula.parent = replace(PARENT, children=[])
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert await client.receive_json() == {"id": 1, "type": "event", "event": {"ended": True}}
    await client.send_json_auto_id({"type": "unsubscribe_events", "subscription": 1})
    assert (await client.receive_json())["success"]
    await coordinator.async_refresh()
    await client.send_json_auto_id({"type": "aula_connect/children"})
    assert (await client.receive_json())["result"] == {"children": []}


@pytest.mark.parametrize(
    ("start", "end", "days"),
    [
        (at(30, 0), at(31, 0), (date(2026, 12, 30), date(2026, 12, 31))),
        (at(30, 0), at(30, 23, 59), (date(2026, 12, 30), date(2026, 12, 31))),
        (at(30, 0), at(30, 0), (date(2026, 12, 30), date(2026, 12, 31))),
        (at(29, 23), at(31, 23), (date(2026, 12, 29), date(2027, 1, 1))),
    ],
    ids=["midnight to midnight", "ends before midnight", "no length", "two days and a bit"],
)
async def test_an_all_day_event_covers_whole_local_days(
    hass: HomeAssistant, start: datetime, end: datetime, days: tuple[date, date]
) -> None:
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    assert all_day_dates(start, end) == days
