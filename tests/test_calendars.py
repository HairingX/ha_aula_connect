"""Tests of the calendars an Aula login shows, read from a made-up Aula account."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
    AulaEasyiqDailyPlan,
    AulaEasyiqEvent,
    AulaEasyiqWeeklyPlan,
    AulaNewsletter,
    AulaWeeklyNewsletter,
    AulaWeeklyPlan,
)
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.aula_connect.const import DOMAIN

from .fake_aula import CHILD, PARENT_AT_SCHOOL, SCHOOL, FakeAula
from .test_init import DATA

TZ = ZoneInfo("Europe/Copenhagen")
NOW = datetime(2026, 12, 28, 10, 0, tzinfo=TZ)
def at(day: int, hour: int, minute: int = 0, month: int = 12, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def event(event_id: int, title: str, start: datetime, end: datetime, **fields: Any) -> AulaCalendarEvent:
    values: dict[str, Any] = {
        "all_day": False,
        "belongs_to_profiles": [],
        "belongs_to_resources": [],
        "created_datetime": at(1, 8),
        "end_datetime": end,
        "has_attachments": False,
        "id": event_id,
        "requires_new_answer": False,
        "response_required": False,
        "start_datetime": start,
        "title": title,
        "type": "event",
    }
    values.update(fields)
    return AulaCalendarEvent(**values)


async def set_up(hass: HomeAssistant, freezer: FrozenDateTimeFactory, loads: bool = True) -> None:
    """Set up an entry of the made-up account at a Monday morning in Copenhagen."""
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    freezer.move_to(NOW)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="account", title="Aula", data={**DATA, "entity_prefix": "Aula"}, version=2, minor_version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is loads
    await hass.async_block_till_done()


async def _events(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    response: Any = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": entity_id, "start_date_time": start.isoformat(), "end_date_time": end.isoformat()},
        blocking=True,
        return_response=True,
    )
    return response[entity_id]["events"]


def _calendars(hass: HomeAssistant) -> list[str]:
    return sorted(state.entity_id for state in hass.states.async_all("calendar"))


async def test_a_child_and_a_parent_each_get_their_calendars(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    assert _calendars(hass) == [
        "calendar.aula_birthdays_alma_school",
        "calendar.aula_easyiq_weekly_plans_alma_school",
        "calendar.aula_events_alma_school",
        "calendar.aula_events_pat_school",
        "calendar.aula_newsletter_alma_school",
        "calendar.aula_weekly_plans_alma_school",
    ]


async def test_an_institution_without_the_widgets_has_only_the_event_calendars(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.widgets = set()
    await set_up(hass, freezer)
    assert _calendars(hass) == ["calendar.aula_events_alma_school", "calendar.aula_events_pat_school"]


async def test_a_lesson_shows_its_teachers_its_room_and_a_substitute(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    teacher = AulaCalendarEventLessonParticipant("substituteTeacher", 1, "JD", "Jane Doe")
    aula.events[SCHOOL.id] = [
        event(
            1,
            "Math",
            at(28, 11),
            at(28, 12),
            type="lesson",
            lesson=AulaCalendarEventLesson("L1", "substitute", [teacher]),
            primary_resource=AulaCalendarEventResource(5, "Room 4"),
        )
    ]
    await set_up(hass, freezer)
    state = hass.states.get("calendar.aula_events_alma_school")
    assert state is not None
    assert state.attributes["message"] == "Math (JD) - Vikar"
    assert state.attributes["description"] == "Jane Doe"
    assert state.attributes["location"] == "Room 4"
    assert state.state == "off"
    freezer.move_to(at(28, 11, 30))
    [found] = await _events(hass, "calendar.aula_events_alma_school", at(28, 0), at(29, 0))
    assert found["summary"] == "Math (JD) - Vikar"


async def test_an_invitation_not_yet_answered_says_so(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.events[SCHOOL.id] = [event(2, "Trip", at(29, 9), at(29, 15), response_status="waiting")]
    await set_up(hass, freezer)
    [found] = await _events(hass, "calendar.aula_events_alma_school", at(29, 0), at(30, 0))
    assert found["description"] == "Status: Afventer svar"


def _meeting(answer_for: int | None) -> AulaCalendarEvent:
    slots = [
        AulaCalendarEventTimeSlotEntryIndex(start_datetime=at(29, 14), end_datetime=at(29, 14, 15)),
        AulaCalendarEventTimeSlotEntryIndex(start_datetime=at(29, 14, 15), end_datetime=at(29, 14, 30)),
    ]
    answers = [] if answer_for is None else [AulaCalendarEventTimeSlotEntryAnswer(answer_for, 1, answer_for, 1)]
    entry = AulaCalendarEventTimeSlotEntry(answers, at(30, 16), 1, at(29, 8), slots)
    return event(
        3,
        "Meeting",
        at(29, 8),
        at(30, 16),
        type="school_home_meeting",
        belongs_to_profiles=[CHILD.id],
        time_slot=AulaCalendarEventTimeSlot([entry]),
    )


async def test_a_booked_meeting_shows_only_the_time_booked(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.events[SCHOOL.id] = [_meeting(SCHOOL.id)]
    await set_up(hass, freezer)
    [found] = await _events(hass, "calendar.aula_events_alma_school", at(28, 0), at(31, 0))
    assert found["summary"] == "👥 Meeting School"
    assert found["start"] == at(29, 14, 15).isoformat()
    assert found["end"] == at(29, 14, 30).isoformat()
    assert found["location"] == "School"
    assert found["description"] == "Medbring Alma Example"


async def test_a_meeting_not_yet_booked_is_marked(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.events[SCHOOL.id] = [_meeting(None)]
    aula.events[PARENT_AT_SCHOOL.id] = [_meeting(None)]
    await set_up(hass, freezer)
    [found] = await _events(hass, "calendar.aula_events_alma_school", at(28, 0), at(31, 0))
    assert found["summary"] == "👥 Meeting <!>"
    assert found["start"] == at(29, 8).isoformat()
    [parents] = await _events(hass, "calendar.aula_events_pat_school", at(28, 0), at(31, 0))
    assert parents["summary"] == "👥 Meeting <!>"


async def test_a_parents_meeting_without_the_child_is_not_in_the_childs_calendar(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    meeting = event(4, "Parents' evening", at(29, 19), at(29, 21), type="parental_meeting")
    aula.events[SCHOOL.id] = [meeting]
    aula.events[PARENT_AT_SCHOOL.id] = [meeting]
    await set_up(hass, freezer)
    assert await _events(hass, "calendar.aula_events_alma_school", at(28, 0), at(31, 0)) == []
    [found] = await _events(hass, "calendar.aula_events_pat_school", at(28, 0), at(31, 0))
    assert found["summary"] == "👥 Parents' evening"


async def test_the_next_event_is_the_calendars_state(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.events[SCHOOL.id] = [
        event(5, "Over", at(28, 8), at(28, 9)),
        event(6, "Now", at(28, 9, 30), at(28, 11)),
    ]
    await set_up(hass, freezer)
    state = hass.states.get("calendar.aula_events_alma_school")
    assert state is not None
    assert (state.state, state.attributes["message"]) == ("on", "⭐ Now")


def _birthday(born: date) -> AulaBirthdayEvent:
    return AulaBirthdayEvent(
        institution_profile_id=31, birthday_date=born, institution_code="S1", full_name="Bo Friend", main_group_name="3A"
    )


async def test_a_birthday_after_new_year_is_in_the_new_year(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.birthdays = [_birthday(date(2017, 1, 3))]
    await set_up(hass, freezer)
    [found] = await _events(hass, "calendar.aula_birthdays_alma_school", at(28, 0), at(5, 0, month=1, year=2027))
    assert (found["start"], found["end"]) == ("2027-01-03", "2027-01-04")
    assert found["summary"] == "🎁 Bo Friend (3A) fylder 10"
    state = hass.states.get("calendar.aula_birthdays_alma_school")
    assert state is not None
    assert state.attributes["start_time"] == "2027-01-03 00:00:00"
    assert state.attributes["message"] == "🎁 Bo Friend (3A) fylder 10"


async def test_a_birthday_on_the_29th_of_february_is_kept_on_the_28th_outside_a_leap_year(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.birthdays = [_birthday(date(2016, 2, 29))]
    await set_up(hass, freezer)
    found = await _events(
        hass, "calendar.aula_birthdays_alma_school", at(1, 0, month=2, year=2027), at(1, 0, month=3, year=2028)
    )
    assert [(item["start"], item["summary"][-2:]) for item in found] == [("2027-02-28", "11"), ("2028-02-29", "12")]


async def test_a_birthday_already_past_this_year_is_next_years(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.birthdays = [_birthday(date(2017, 5, 1)), _birthday(date(2017, 3, 1))]
    await set_up(hass, freezer)
    state = hass.states.get("calendar.aula_birthdays_alma_school")
    assert state is not None
    assert state.attributes["start_time"] == "2027-03-01 00:00:00"


async def test_a_weekly_plans_task_is_an_all_day_event_with_a_short_summary(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    long_task = AulaDailyPlanTask("Teacher", "Read page 4\nand page 5", "3A", 41, "Danish", "task")
    comment = AulaDailyPlanTask("", "x" * 80, "", 42, "", "comment", title=None)
    aula.weekly_plans = [
        AulaWeeklyPlan(
            from_date=date(2026, 12, 28),
            name="Week 53",
            to_date=date(2027, 1, 3),
            unilogin="alma",
            daily_plans=[
                AulaDailyPlan(date(2026, 12, 25), [AulaDailyPlanTask("", "Past", "", 40, "", "comment")]),
                AulaDailyPlan(date(2026, 12, 29), [long_task, comment]),
            ],
        )
    ]
    await set_up(hass, freezer)
    found = await _events(hass, "calendar.aula_weekly_plans_alma_school", at(29, 0), at(30, 0))
    assert [(item["summary"], item["start"], item["end"]) for item in found] == [
        ("🔴 [Danish] Read page 4 ...", "2026-12-29", "2026-12-30"),
        ("ℹ️ " + "x" * 73 + "...", "2026-12-29", "2026-12-30"),
    ]
    assert found[0]["description"] == "Read page 4\nand page 5\n3A\nTeacher"
    state = hass.states.get("calendar.aula_weekly_plans_alma_school")
    assert state is not None
    assert state.attributes["message"] == "🔴 [Danish] Read page 4 ..."


async def test_an_easyiq_event_shows_its_owner_and_description(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    item = AulaEasyiqEvent("e1", "", "Swimming\nBring a towel", "Coach", "5", at(29, 10), at(29, 11))
    aula.easyiq_plans = [AulaEasyiqWeeklyPlan("Week 53", date(2026, 12, 28), date(2027, 1, 3), [
        AulaEasyiqDailyPlan(date(2026, 12, 29), [item])
    ])]
    await set_up(hass, freezer)
    [found] = await _events(hass, "calendar.aula_easyiq_weekly_plans_alma_school", at(29, 0), at(30, 0))
    assert found["summary"] == "Swimming ..."
    assert found["description"] == "Coach\nSwimming\nBring a towel"
    state = hass.states.get("calendar.aula_easyiq_weekly_plans_alma_school")
    assert state is not None
    assert state.attributes["message"] == "Swimming ..."


async def test_a_newsletter_spans_its_week_with_its_html(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    letter = AulaNewsletter("n1", "<p>News</p>", "School")
    aula.newsletters = [AulaWeeklyNewsletter("Alma", date(2026, 12, 28), date(2027, 1, 3), [letter])]
    await set_up(hass, freezer)
    [found] = await _events(hass, "calendar.aula_newsletter_alma_school", at(28, 0), at(29, 0))
    assert (found["start"], found["end"]) == ("2026-12-28", "2027-01-04")
    assert found["summary"] == "Newsletter - Alma (School)"
    assert found["description"] == "<p>News</p>"
    state = hass.states.get("calendar.aula_newsletter_alma_school")
    assert state is not None
    assert state.state == "on"


async def test_calendars_without_anything_ahead_are_off(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    for entity_id in _calendars(hass):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "off", entity_id



async def test_a_lesson_awaiting_an_answer_lists_its_teachers_then_the_status(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    teacher = AulaCalendarEventLessonParticipant("primaryTeacher", 1, "", "Jane Doe")
    aula.events[SCHOOL.id] = [
        event(
            7,
            "Math",
            at(29, 9),
            at(29, 10),
            type="lesson",
            lesson=AulaCalendarEventLesson("L2", "normal", [teacher]),
            response_status="waiting",
        )
    ]
    await set_up(hass, freezer)
    [found] = await _events(hass, "calendar.aula_events_alma_school", at(29, 0), at(30, 0))
    assert found["summary"] == "Math"
    assert found["description"] == "Jane Doe\nStatus: Afventer svar"


async def test_a_task_with_a_title_shows_the_title_and_then_the_content(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    task = AulaDailyPlanTask("", "Pages 4-5", "", 43, "", "task", title="Homework")
    aula.weekly_plans = [
        AulaWeeklyPlan(date(2026, 12, 28), "Week 53", date(2027, 1, 3), "alma", [AulaDailyPlan(date(2026, 12, 29), [task])])
    ]
    await set_up(hass, freezer)
    [found] = await _events(hass, "calendar.aula_weekly_plans_alma_school", at(29, 0), at(30, 0))
    assert found["summary"] == "🔴 Homework"
    assert found["description"] == "Homework\nPages 4-5"


async def test_the_calendars_show_what_aula_says_a_day_later(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    aula.birthdays = [_birthday(date(2017, 1, 3))]
    aula.events[SCHOOL.id] = [event(8, "Trip", at(30, 9), at(30, 15))]
    aula.events[PARENT_AT_SCHOOL.id] = [event(9, "Meeting", at(30, 19), at(30, 20))]
    aula.weekly_plans = [
        AulaWeeklyPlan(date(2026, 12, 28), "Week 53", date(2027, 1, 3), "alma", [
            AulaDailyPlan(date(2026, 12, 30), [AulaDailyPlanTask("", "Read", "", 44, "", "comment")])
        ])
    ]
    aula.easyiq_plans = [AulaEasyiqWeeklyPlan("Week 53", date(2026, 12, 28), date(2027, 1, 3), [
        AulaEasyiqDailyPlan(date(2026, 12, 30), [AulaEasyiqEvent("e2", "Gym", "", "", "5", at(30, 10), at(30, 11))])
    ])]
    aula.newsletters = [AulaWeeklyNewsletter("Alma", date(2026, 12, 28), date(2027, 1, 3), [AulaNewsletter("n2", "", "")])]

    freezer.move_to(at(29, 10, 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    messages = {
        entity_id: (state.attributes.get("message") if (state := hass.states.get(entity_id)) else None)
        for entity_id in _calendars(hass)
    }
    assert messages == {
        "calendar.aula_birthdays_alma_school": "🎁 Bo Friend (3A) fylder 10",
        "calendar.aula_easyiq_weekly_plans_alma_school": "Gym",
        "calendar.aula_events_alma_school": "⭐ Trip",
        "calendar.aula_events_pat_school": "⭐ Meeting",
        "calendar.aula_newsletter_alma_school": "Newsletter - Alma",
        "calendar.aula_weekly_plans_alma_school": "ℹ️ Read",
    }


async def test_calendars_aula_cannot_be_read_at_start_are_tried_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    with patch.object(aula, "login", side_effect=[aula.login(), ConnectionError("down")]):
        await set_up(hass, freezer, loads=False)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_calendars_whose_login_aula_no_longer_accepts_ask_to_log_in_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    with patch.object(aula, "login", side_effect=[aula.login(), AulaCredentialError("expired")]):
        await set_up(hass, freezer, loads=False)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == ["reauth"]


@pytest.mark.parametrize(
    "method",
    ["get_birthday_events", "get_calendar_events", "get_weekly_plans", "get_easyiq_weekly_plans", "get_newsletters"],
)
async def test_a_calendar_aula_cannot_read_keeps_what_it_had_and_says_so_once(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    method: str,
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, method, side_effect=ConnectionError("down")):
        for day in (29, 30):
            freezer.move_to(at(day, 10, 1))
            async_fire_time_changed(hass)
            await hass.async_block_till_done(wait_background_tasks=True)
    assert caplog.text.count("Aula's calendars could be read only in part") == 1
    assert all(state.state != "unavailable" for state in hass.states.async_all("calendar"))
    freezer.move_to(at(31, 10, 2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert caplog.text.count("Aula's calendars can be read in full again") == 1


async def test_calendars_aula_cannot_read_three_times_running_become_unavailable(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, "get_calendar_events", side_effect=ConnectionError("down")):
        for hours in (3, 6, 9):
            freezer.move_to(at(28, 10 + hours, 1))
            async_fire_time_changed(hass)
            await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("calendar.aula_events_alma_school")
    assert state is not None
    assert state.state == "unavailable"


async def test_calendars_whose_login_fails_later_become_unavailable(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, "login", side_effect=ConnectionError("down")):
        freezer.move_to(at(28, 10, 11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("calendar.aula_events_alma_school")
    assert state is not None
    assert state.state == "unavailable"


async def test_unloading_the_entry_removes_its_calendars(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(state.state == "unavailable" for state in hass.states.async_all("calendar"))


@pytest.mark.parametrize(
    "method",
    ["get_birthday_events", "get_calendar_events", "get_weekly_plans", "get_easyiq_weekly_plans", "get_newsletters"],
)
async def test_a_calendar_whose_login_aula_no_longer_accepts_asks_to_log_in_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, method: str
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, method, side_effect=AulaCredentialError("expired")):
        freezer.move_to(at(29, 10, 1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow.get("context", {}).get("source") for flow in flows] == ["reauth"]


@pytest.mark.parametrize(
    ("error", "state"),
    [(ConnectionError("down"), ConfigEntryState.SETUP_RETRY), (AulaCredentialError("expired"), ConfigEntryState.SETUP_ERROR)],
)
async def test_calendars_that_cannot_reach_aula_at_start_stop_the_setup(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, error: Exception, state: ConfigEntryState
) -> None:
    with patch.object(aula, "connection_check", side_effect=[None, error]):
        await set_up(hass, freezer, loads=False)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.state is state
