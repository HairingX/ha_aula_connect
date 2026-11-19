"""Tests of each child's school day, picture, homework and vacation requests, and of the events of
new messages, photos and calendar events."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aula_connect.api.models.aula_message_thread_models import AulaMessagePreview
from aula_connect.api.models.module import (
    AulaAlbumNotification,
    AulaCalendarEventNotification,
    AulaDailyPlan,
    AulaDailyPlanTask,
    AulaGalleryNotification,
    AulaMessageThread,
    AulaProfilePicture,
    AulaThreadRegardingChild,
    AulaVacationRequest,
    AulaWeeklyPlan,
)
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.image import async_get_image
from homeassistant.components.todo import TodoItem, TodoItemStatus
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import async_fire_time_changed
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from custom_components.aula_connect.const import DOMAIN
from custom_components.aula_connect.todo import AulaHomeworkList

from .fake_aula import CHILD, SCHOOL, FakeAula
from .test_calendars import at, event, set_up


def _state(hass: HomeAssistant, entity_id: str) -> Any:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, moment: datetime) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _read_again(hass: HomeAssistant) -> None:
    [entry] = hass.config_entries.async_entries(DOMAIN)
    await entry.runtime_data.data_coordinator.async_refresh()
    await hass.async_block_till_done()


def _lesson(event_id: int, start: datetime, end: datetime) -> Any:
    return event(event_id, "Math", start, end, type="lesson")


async def test_a_school_day_has_its_first_lessons_start_and_its_last_lessons_end(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.events[SCHOOL.id] = [
        _lesson(1, at(28, 8), at(28, 9)),
        _lesson(2, at(28, 13), at(28, 14, 30)),
        _lesson(3, at(29, 8), at(29, 9)),
        event(4, "Juleferie", at(29, 0), at(30, 0), type="holiday", all_day=True),
        _lesson(5, at(30, 10), at(30, 11)),
    ]
    await set_up(hass, freezer)
    today = _state(hass, "binary_sensor.aula_school_today_alma_school")
    assert today.state == "on"
    assert (today.attributes["start"], today.attributes["end"]) == (at(28, 8), at(28, 14, 30))
    assert _state(hass, "sensor.aula_school_start_alma_school").state == "2026-12-28T07:00:00+00:00"
    assert _state(hass, "sensor.aula_school_end_alma_school").state == "2026-12-28T13:30:00+00:00"
    tomorrow = _state(hass, "binary_sensor.aula_school_tomorrow_alma_school")
    assert tomorrow.state == "off" and tomorrow.attributes["start"] is None

    await _poll(hass, freezer, at(29, 0, 5))
    assert _state(hass, "binary_sensor.aula_school_today_alma_school").state == "off"
    assert _state(hass, "sensor.aula_school_start_alma_school").state == "unknown"
    assert _state(hass, "binary_sensor.aula_school_tomorrow_alma_school").state == "on"


async def test_a_school_day_is_unknown_and_unavailable_while_the_events_cannot_be_read(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    with patch.object(aula, "get_calendar_events", AsyncMock(side_effect=ConnectionError("down"))):
        await set_up(hass, freezer)
    assert _state(hass, "binary_sensor.aula_school_today_alma_school").state == "unavailable"
    assert _state(hass, "sensor.aula_school_end_alma_school").state == "unavailable"


async def test_a_school_day_entity_asks_the_calendar_to_be_read_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    assert await async_setup_component(hass, "homeassistant", {})
    [entry] = hass.config_entries.async_entries(DOMAIN)
    with patch.object(entry.runtime_data.calendar_coordinator, "async_request_refresh", AsyncMock()) as refresh:
        await hass.services.async_call(
            "homeassistant", "update_entity", {"entity_id": "binary_sensor.aula_school_today_alma_school"}, blocking=True
        )
    refresh.assert_awaited_once()


def vacation_request(response_id: int, **fields: Any) -> AulaVacationRequest:
    values: dict[str, Any] = {
        "id": response_id,
        "response_id": response_id,
        "child_id": CHILD.id,
        "title": "Juleferie",
        "start_date": date(2027, 2, 15),
        "end_date": date(2027, 2, 19),
        "response_deadline": date(2027, 1, 10),
        "note_to_guardian": "Svar venligst",
        "missing_answer": True,
        "is_editable": True,
        "is_presence_times_required": False,
    }
    values.update(fields)
    return AulaVacationRequest(**values)


async def test_a_vacation_request_awaiting_an_answer_is_counted_with_its_first_deadline(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.vacation_requests = [
        vacation_request(1),
        vacation_request(2, response_deadline=date(2027, 1, 5)),
        vacation_request(3, response_deadline=None),
        vacation_request(4, missing_answer=False),
        vacation_request(5, response_deadline=date(2026, 12, 1)),
        vacation_request(6, child_id=99),
    ]
    await set_up(hass, freezer)
    state = _state(hass, "binary_sensor.aula_vacation_request_alma_school")
    assert state.state == "on"
    assert (state.attributes["count"], state.attributes["deadline"]) == (3, date(2027, 1, 5))

    aula.vacation_requests = []
    await _read_again(hass)
    state = _state(hass, "binary_sensor.aula_vacation_request_alma_school")
    assert (state.state, state.attributes["count"], state.attributes["deadline"]) == ("off", 0, None)


async def test_the_vacation_requests_are_unavailable_while_they_cannot_be_read(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, "get_vacation_requests", AsyncMock(side_effect=ConnectionError("down"))):
        await _read_again(hass)
    assert _state(hass, "binary_sensor.aula_vacation_request_alma_school").state == "unavailable"


PICTURE_URL = "https://media.example.com/alma.jpg?Signature=s3cr3t"


def _serve(aioclient_mock: AiohttpClientMocker, **answer: Any) -> None:
    """Answer a GET of the picture's link as `answer` says."""
    get: Callable[..., None] = getattr(aioclient_mock, "get")
    get(PICTURE_URL, **answer)


async def test_a_childs_picture_is_read_anew_when_shown_and_kept_a_while(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    aula.parent.children[0] = replace(CHILD, profile_picture=AulaProfilePicture(id=1, key="a"))
    aula.picture = AulaProfilePicture(id=1, key="a", url=PICTURE_URL)
    _serve(aioclient_mock, content=b"\xff\xd8\xff\xe0picture", headers={"Content-Type": "image/jpeg"})
    try:
        await set_up(hass, freezer)
        state = _state(hass, "image.aula_picture_alma_school")
        assert state.attributes["child_id"] == CHILD.id
        assert PICTURE_URL not in str(state.attributes)
        first = state.state

        image = await async_get_image(hass, "image.aula_picture_alma_school")
        assert (image.content, image.content_type) == (b"\xff\xd8\xff\xe0picture", "image/jpeg")
        await async_get_image(hass, "image.aula_picture_alma_school")
        assert aula.reads.count("get_profile_picture") == 1

        freezer.tick(timedelta(minutes=11))
        await async_get_image(hass, "image.aula_picture_alma_school")
        assert aula.reads.count("get_profile_picture") == 2

        aula.parent.children[0] = replace(CHILD, profile_picture=AulaProfilePicture(id=2, key="b"))
        freezer.tick(timedelta(seconds=1))
        await _read_again(hass)
        assert _state(hass, "image.aula_picture_alma_school").state != first
        await async_get_image(hass, "image.aula_picture_alma_school")
        assert aula.reads.count("get_profile_picture") == 3

        aula.parent.children[0] = CHILD
        await _read_again(hass)
        assert _state(hass, "image.aula_picture_alma_school").state == "unknown"
    finally:
        aula.parent.children[0] = CHILD


async def test_a_picture_that_cannot_be_read_is_none_and_its_link_is_not_logged(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await set_up(hass, freezer)
    assert _state(hass, "image.aula_picture_alma_school").state == "unknown"
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, "image.aula_picture_alma_school")

    aula.picture = AulaProfilePicture(id=1, key="a", url=PICTURE_URL)
    _serve(aioclient_mock, status=403)
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, "image.aula_picture_alma_school")
    aula.error = ConnectionError("url: /api?access_token=s3cr3t")
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, "image.aula_picture_alma_school")
    assert "s3cr3t" not in caplog.text
    assert "picture of image.aula_picture_alma_school could not be read" in caplog.text


async def test_a_picture_without_an_image_type_keeps_the_type_it_had(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    aula.picture = AulaProfilePicture(id=1, key="a", url=PICTURE_URL)
    _serve(aioclient_mock, content=b"\xff\xd8\xff\xe0picture", headers={"Content-Type": "text/html"})
    await set_up(hass, freezer)
    image = await async_get_image(hass, "image.aula_picture_alma_school")
    assert image.content_type == "image/jpeg"


def _task(task_id: int, content: str, **fields: Any) -> AulaDailyPlanTask:
    values: dict[str, Any] = {"author": "JD", "content": content, "group": "3A", "id": task_id, "pill": "Dansk", "type": "task"}
    values.update(fields)
    return AulaDailyPlanTask(**values)


def _homework() -> list[AulaWeeklyPlan]:
    return [
        AulaWeeklyPlan(
            date(2026, 12, 28),
            "Week 53",
            date(2027, 1, 3),
            "alma",
            [
                AulaDailyPlan(date(2026, 12, 29), [_task(2, "Read page 4\nand page 5"), _task(3, "Bring a ball", type="comment")]),
                AulaDailyPlan(date(2026, 12, 28), [_task(1, "x" * 80, pill="", title=None)]),
            ],
        )
    ]


async def _items(hass: HomeAssistant) -> list[dict[str, Any]]:
    response: Any = await hass.services.async_call(
        "todo", "get_items", {"entity_id": "todo.aula_homework_alma_school"}, blocking=True, return_response=True
    )
    return response["todo.aula_homework_alma_school"]["items"]


async def test_the_homework_is_the_weekly_plans_tasks_due_on_their_day(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.weekly_plans = _homework()
    await set_up(hass, freezer)
    assert _state(hass, "todo.aula_homework_alma_school").state == "2"
    assert await _items(hass) == [
        {"uid": "1", "summary": "x" * 72 + "...", "status": "needs_action", "due": "2026-12-28", "description": "x" * 80},
        {"uid": "2", "summary": "[Dansk] Read page 4", "status": "needs_action", "due": "2026-12-29", "description": "Read page 4\nand page 5"},
    ]


async def test_homework_ticked_is_remembered_at_home_only_and_only_ticked(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.weekly_plans = _homework()
    await set_up(hass, freezer)
    await hass.services.async_call(
        "todo", "update_item", {"entity_id": "todo.aula_homework_alma_school", "item": "2", "status": "completed"}, blocking=True
    )
    assert [item["status"] for item in await _items(hass)] == ["needs_action", "completed"]
    assert _state(hass, "todo.aula_homework_alma_school").state == "1"
    assert aula.writes == []

    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert [item["status"] for item in await _items(hass)] == ["needs_action", "completed"]

    await hass.services.async_call(
        "todo", "update_item", {"entity_id": "todo.aula_homework_alma_school", "item": "2", "status": "needs_action"}, blocking=True
    )
    assert [item["status"] for item in await _items(hass)] == ["needs_action", "needs_action"]

    with pytest.raises(ServiceValidationError) as renamed:
        await hass.services.async_call(
            "todo", "update_item", {"entity_id": "todo.aula_homework_alma_school", "item": "2", "rename": "Other"}, blocking=True
        )
    assert renamed.value.translation_key == "homework_only_ticked"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("todo", "add_item", {"entity_id": "todo.aula_homework_alma_school", "item": "More"}, blocking=True)


async def test_homework_not_on_the_list_cannot_be_ticked(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    platform = next(found for found in entity_platform.async_get_platforms(hass, DOMAIN) if found.domain == "todo")
    homework = platform.entities["todo.aula_homework_alma_school"]
    assert isinstance(homework, AulaHomeworkList)
    with pytest.raises(ServiceValidationError) as missing:
        await homework.async_update_todo_item(TodoItem(uid="9", status=TodoItemStatus.COMPLETED))
    assert missing.value.translation_key == "homework_not_found"


async def test_an_institution_without_weekly_plans_has_no_homework(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.widgets = set()
    await set_up(hass, freezer)
    assert hass.states.get("todo.aula_homework_alma_school") is None


def message_thread(thread_id: int, latest: str, **fields: Any) -> AulaMessageThread:
    values: dict[str, Any] = {
        "extra_recipients_count": 0,
        "id": thread_id,
        "institution_code": "S1",
        "is_archived": False,
        "is_thread_or_subscription_deleted": False,
        "marked": False,
        "muted": False,
        "read": False,
        "recipients": [],
        "regarding_children": [AulaThreadRegardingChild("Alma Example", CHILD.profile_id, "AE")],
        "sensitive": False,
        "started_datetime": at(1, 8),
        "subject": f"Thread {thread_id}",
        "latest_message": AulaMessagePreview(latest, at(28, 9)),
    }
    values.update(fields)
    return AulaMessageThread(**values)


def photo_and_event() -> list[Any]:
    """A gallery's and an album's new photos, and an invitation to a calendar event."""
    notification: dict[str, Any] = {
        "notification_area": "Gallery",
        "notification_event_type": "NewMedia",
        "notification_type": "Badge",
        "expires": at(30, 8),
        "triggered": at(28, 9),
        "institution_code": "S1",
        "institution_profile_id": 21,
    }
    calendar: dict[str, Any] = {**notification, "notification_area": "Calendar"}
    return [
        AulaGalleryNotification(**notification, notification_id="n1", album_id=7, media_ids=[1, 2], related_institution="School"),
        AulaAlbumNotification(**notification, notification_id="n2", album_id=8, media_id=3, related_institution="School"),
        AulaCalendarEventNotification(
            **calendar,
            notification_id="n3",
            event_id=5,
            title="Play",
            start_datetime=at(30, 10),
            end_datetime=at(30, 11),
            is_all_day_event=False,
        ),
    ]


def _fired(hass: HomeAssistant) -> list[State]:
    """The new states of the event entities from now on, each a fired event."""
    fired: list[State] = []

    @callback
    def changed(found: Event[EventStateChangedData]) -> None:
        old, new = found.data["old_state"], found.data["new_state"]
        if new is not None and new.domain == "event" and (old is None or old.state != new.state):
            fired.append(new)

    hass.bus.async_listen(EVENT_STATE_CHANGED, changed)
    return fired


async def test_a_new_message_photo_and_event_are_told_once_and_what_was_there_before_never(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.message_threads = [message_thread(1, "m1")]
    await set_up(hass, freezer)
    for entity_id in ("event.aula_new_message", "event.aula_new_photo", "event.aula_new_event"):
        assert _state(hass, entity_id).state == "unknown"

    aula.message_threads = [
        message_thread(1, "m2"),
        message_thread(2, "m3", sensitive=True, subject="Secret"),
        message_thread(3, "m4", read=True),
        message_thread(4, "m5", latest_message=None),
    ]
    aula.notifications = photo_and_event()
    await _read_again(hass)

    message = _state(hass, "event.aula_new_message")
    assert message.attributes["event_type"] == "new_message"
    assert (message.attributes["thread_id"], message.attributes["subject"], message.attributes["sensitive"]) == (2, None, True)
    assert message.attributes["children"] == ["Alma Example"]
    photo = _state(hass, "event.aula_new_photo")
    assert (photo.attributes["album_id"], photo.attributes["count"]) == (8, 1)
    calendar = _state(hass, "event.aula_new_event")
    assert (calendar.attributes["event_id"], calendar.attributes["title"], calendar.attributes["all_day"]) == (5, "Play", False)
    assert calendar.attributes["start"] == at(30, 10).isoformat()

    fired = _fired(hass)
    await _read_again(hass)
    assert fired == []


async def test_a_new_message_counts_each_one_and_not_the_reply_sent_from_home_assistant(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    fired = _fired(hass)
    aula.message_threads = [message_thread(1, "m1"), message_thread(2, "m2")]
    await _read_again(hass)
    assert [found.attributes["thread_id"] for found in fired if found.entity_id == "event.aula_new_message"] == [1, 2]

    [entry] = hass.config_entries.async_entries(DOMAIN)
    entry.runtime_data.data_coordinator.note_own_reply(1)
    aula.message_threads = [message_thread(1, "own"), message_thread(2, "m2")]
    freezer.tick(timedelta(minutes=1))
    await _read_again(hass)
    assert len([found for found in fired if found.entity_id == "event.aula_new_message"]) == 2

    aula.message_threads = [message_thread(1, "teacher"), message_thread(2, "m2")]
    freezer.tick(timedelta(minutes=1))
    await _read_again(hass)
    assert len([found for found in fired if found.entity_id == "event.aula_new_message"]) == 3


async def test_the_events_are_unavailable_while_what_they_tell_of_cannot_be_read(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    with patch.object(aula, "get_notifications", AsyncMock(side_effect=ConnectionError("down"))):
        await _read_again(hass)
    assert _state(hass, "event.aula_new_photo").state == "unavailable"
    assert _state(hass, "event.aula_new_message").state == "unknown"
    await _read_again(hass)
    assert _state(hass, "event.aula_new_photo").state == "unknown"
