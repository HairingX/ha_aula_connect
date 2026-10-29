"""Tests of the sensors and binary sensors of an Aula login, read from a made-up Aula account."""

from __future__ import annotations

import json
from datetime import time
from pathlib import Path
from typing import Any, TypeVar

import pytest
from aula_connect.api.models.module import (
    AulaAlbumNotification,
    AulaCalendarEventNotification,
    AulaDailyOverview,
    AulaGalleryNotification,
    AulaLocation,
    AulaMessageText,
    AulaMessageThread,
    AulaPostNotification,
    AulaPresenceNotification,
    AulaThreadRecipient,
    NotificationType,
)
from aula_connect.api.models.aula_message_thread_models import AulaMessagePreview
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from .fake_aula import SCHOOL, FakeAula
from .test_calendars import at, event, set_up

NOTIFICATION = TypeVar("NOTIFICATION")

TRANSLATIONS = Path(__file__).parent.parent / "custom_components" / "aula_connect" / "translations"
COMMON_ATTRIBUTES = {"friendly_name", "icon", "device_class", "unit_of_measurement", "state_class", "options"}


@pytest.mark.parametrize(
    ("status", "presence"),
    [(0, "not_present"), (1, "not_present"), (2, "not_present"), (3, "present"), (8, "not_present"), (9, "unknown")],
    ids=["not arrived", "sick", "reported absent", "present", "checked out", "unknown"],
)
async def test_presence_says_whether_the_child_is_at_the_institution(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, status: int, presence: str
) -> None:
    aula.daily_overviews = [AulaDailyOverview(id=1, institution_profile=SCHOOL, status=status)]
    await set_up(hass, freezer)
    state = hass.states.get("sensor.aula_presence_alma_school")
    assert state is not None
    assert state.state == presence
    status_state = hass.states.get("sensor.aula_status_alma_school")
    assert status_state is not None
    assert status_state.state == str(status)


@pytest.mark.parametrize("language", ["en", "da"])
async def test_every_attribute_has_a_name(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory, language: str
) -> None:
    await set_up(hass, freezer)
    translations = json.loads((TRANSLATIONS / f"{language}.json").read_text())["entity"]
    registry = er.async_get(hass)
    for state in hass.states.async_all(["sensor", "binary_sensor"]):
        entry = registry.async_get(state.entity_id)
        assert entry is not None and entry.translation_key is not None
        named = translations[state.domain][entry.translation_key].get("state_attributes", {})
        assert set(state.attributes) - COMMON_ATTRIBUTES <= set(named), state.entity_id


async def test_the_status_shows_where_the_child_is(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    location = AulaLocation(description="By the lake", id=4, name="Forest", icon="tree")
    aula.daily_overviews = [AulaDailyOverview(id=1, institution_profile=SCHOOL, status=7, location=location)]
    await set_up(hass, freezer)
    state = hass.states.get("sensor.aula_status_alma_school")
    assert state is not None
    assert state.state == "7"
    assert state.attributes["location_name"] == "Forest"
    assert state.attributes["location_description"] == "By the lake"
    assert state.attributes["location_icon"] == "tree"
    assert state.attributes["institution_name"] == "School"


async def test_a_status_aula_has_not_documented_is_unknown(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.daily_overviews = [AulaDailyOverview(id=1, institution_profile=SCHOOL, status=12)]
    await set_up(hass, freezer)
    state = hass.states.get("sensor.aula_status_alma_school")
    assert state is not None
    assert state.state == "unknown"
    assert state.attributes["icon"] == "mdi:map-marker-off"
    presence = hass.states.get("sensor.aula_presence_alma_school")
    assert presence is not None and presence.state == "unknown"


@pytest.mark.parametrize(
    ("status", "check_in", "check_out", "minutes", "icon"),
    [
        (8, time(8, 0), time(14, 30), "390", "mdi:timer-check"),
        (3, time(8, 0), None, "120", "mdi:timer-play"),
        (0, time(11, 0), None, "0", "mdi:timer"),
        (1, None, None, "0", "mdi:timer-remove"),
        (12, None, None, "0", "mdi:timer-alert"),
    ],
    ids=["checked out", "checked in", "before arrival", "sick", "unknown"],
)
async def test_the_presence_duration_counts_the_minutes_since_check_in(
    hass: HomeAssistant,
    aula: FakeAula,
    freezer: FrozenDateTimeFactory,
    status: int,
    check_in: time | None,
    check_out: time | None,
    minutes: str,
    icon: str,
) -> None:
    aula.daily_overviews = [
        AulaDailyOverview(
            id=1, institution_profile=SCHOOL, status=status, check_in_time=check_in, check_out_time=check_out
        )
    ]
    await set_up(hass, freezer)
    state = hass.states.get("sensor.aula_present_duration_alma_school")
    assert state is not None
    assert state.state == minutes
    assert state.attributes["icon"] == icon
    assert state.attributes["check_in_time"] == check_in
    assert state.attributes["check_out_time"] == check_out


async def test_the_presence_duration_is_unknown_once_the_daily_overview_is_gone(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.daily_overviews = [
        AulaDailyOverview(id=1, institution_profile=SCHOOL, status=8, check_in_time=time(8), check_out_time=time(9))
    ]
    await set_up(hass, freezer)
    aula.daily_overviews = []
    await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": "sensor.aula_present_duration_alma_school"}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.aula_present_duration_alma_school")
    assert state is not None
    assert state.state == "unknown"


def thread(subject: str, **fields: Any) -> AulaMessageThread:
    values: dict[str, Any] = {
        "extra_recipients_count": 0,
        "id": len(subject),
        "institution_code": "S1",
        "is_archived": False,
        "is_thread_or_subscription_deleted": False,
        "marked": False,
        "muted": False,
        "read": False,
        "recipients": [],
        "regarding_children": [],
        "sensitive": False,
        "started_datetime": at(27, 9),
        "subject": subject,
    }
    values.update(fields)
    return AulaMessageThread(**values)


def recipient(name: str) -> AulaThreadRecipient:
    return AulaThreadRecipient(answer_directly_name=name, full_name=f"{name} Example", metadata="")


def notification(kind: type[NOTIFICATION], badge: bool = True, **fields: Any) -> NOTIFICATION:
    values: dict[str, Any] = {
        "notification_area": "",
        "notification_event_type": "",
        "notification_id": str(len(fields)),
        "notification_type": NotificationType.BADGE if badge else NotificationType.ALERT,
        "expires": at(31, 9),
        "triggered": at(27, 9),
        "institution_code": "S1",
        "institution_profile_id": 11,
    }
    values.update(fields)
    return kind(**values)


def _attributes(hass: HomeAssistant, entity_id: str, *names: str) -> tuple[str, dict[str, Any]]:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state, {name: state.attributes[name] for name in names}


async def test_the_unread_message_describes_the_first_thread_not_muted(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    latest = AulaMessagePreview(id="m1", send_datetime=at(27, 9, 30), text=AulaMessageText(html="<p>Bring boots</p>"))
    aula.message_threads = [
        thread("Read", read=True),
        thread("Muted", muted=True),
        thread(
            "Trip",
            recipients=[recipient("Jane"), recipient("John")],
            extra_recipients_count=2,
            marked=True,
            latest_message=latest,
        ),
        thread("Later"),
    ]
    await set_up(hass, freezer)
    assert _attributes(
        hass, "binary_sensor.aula_unread_message", "total", "subject", "text", "timestamp", "recipients", "muted", "marked"
    ) == (
        "on",
        {
            "total": 3,
            "subject": "Trip",
            "text": "<p>Bring boots</p>",
            "timestamp": at(27, 9, 30),
            "recipients": "Jane, John, +2",
            "muted": False,
            "marked": True,
        },
    )


async def test_only_muted_unread_messages_describe_the_first_of_them(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    preview = AulaMessagePreview(id="m1", send_datetime=at(27, 9, 30))
    aula.message_threads = [thread("Muted", muted=True, latest_message=preview), thread("Also muted", muted=True)]
    await set_up(hass, freezer)
    assert _attributes(hass, "binary_sensor.aula_unread_message", "total", "subject", "text", "timestamp", "muted") == (
        "on",
        {"total": 2, "subject": "Muted", "text": None, "timestamp": at(27, 9, 30), "muted": True},
    )


async def test_a_sensitive_message_shows_neither_its_subject_nor_its_text(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    latest = AulaMessagePreview(id="m1", send_datetime=at(27, 9, 30), text=AulaMessageText(html="<p>Private</p>"))
    aula.message_threads = [thread("Private matter", sensitive=True, latest_message=latest)]
    await set_up(hass, freezer)
    assert _attributes(hass, "binary_sensor.aula_unread_message", "subject", "text", "sensitive") == (
        "on",
        {
            "subject": "Sensitive",
            "text": "This message is sensitive. Please log into Aula to read it.",
            "sensitive": True,
        },
    )


async def test_recipients_aula_does_not_name_are_counted(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.message_threads = [thread("To many", extra_recipients_count=3)]
    await set_up(hass, freezer)
    assert _attributes(hass, "binary_sensor.aula_unread_message", "recipients") == ("on", {"recipients": "+3"})


async def test_photos_and_albums_are_counted_as_unseen(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.notifications = [
        notification(AulaGalleryNotification, album_id=1, media_ids=[1, 2, 3], related_institution="School"),
        notification(AulaAlbumNotification, album_id=2, media_id=4, related_institution="School"),
        notification(AulaPostNotification, title="Not a photo"),
    ]
    await set_up(hass, freezer)
    assert _attributes(hass, "binary_sensor.aula_unread_gallery", "total") == ("on", {"total": 4})


async def test_an_unread_calendar_event_prefers_an_alert_and_counts_the_badges(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    def invitation(title: str, badge: bool) -> AulaCalendarEventNotification:
        return notification(
            AulaCalendarEventNotification,
            badge,
            event_id=len(title),
            title=title,
            is_all_day_event=True,
            start_datetime=at(30, 0),
            end_datetime=at(31, 0),
        )

    aula.notifications = [invitation("Badge", True), invitation("Alert", False), invitation("Second alert", False)]
    await set_up(hass, freezer)
    assert _attributes(
        hass, "binary_sensor.aula_unread_calendar", "total", "alert", "title", "all_day", "start_datetime", "end_datetime"
    ) == (
        "on",
        {
            "total": 1,
            "alert": True,
            "title": "Alert",
            "all_day": True,
            "start_datetime": at(30, 0),
            "end_datetime": at(31, 0),
        },
    )


async def test_an_unread_post_prefers_an_alert_and_counts_the_badges(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.notifications = [
        notification(AulaPostNotification, True, title="First"),
        notification(AulaPostNotification, True, title="Second"),
        notification(AulaPostNotification, False, title="Alert"),
    ]
    await set_up(hass, freezer)
    assert _attributes(hass, "binary_sensor.aula_unread_post", "total", "alert", "title") == (
        "on",
        {"total": 2, "alert": True, "title": "Alert"},
    )


async def test_an_unanswered_presence_request_prefers_an_alert_and_counts_the_badges(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    def request(name: str, badge: bool) -> AulaPresenceNotification:
        return notification(
            AulaPresenceNotification,
            badge,
            end_datetime=at(31, 0),
            is_presence_times_required=False,
            message_text=f"Will {name} be there?",
            related_child_institution_profile_id=11,
            related_child_name="Alma",
            response_deadline=at(29, 12),
            start_datetime=at(30, 0),
            vacation_registration_response_id=len(name),
            vacation_request_name=name,
        )

    aula.notifications = [request("Christmas", True), request("Winter", True), request("Easter", False)]
    await set_up(hass, freezer)
    assert _attributes(
        hass, "binary_sensor.aula_unread_presence", "total", "alert", "title", "text", "start_datetime", "end_datetime"
    ) == (
        "on",
        {
            "total": 2,
            "alert": True,
            "title": "Easter",
            "text": "Will Easter be there?",
            "start_datetime": at(30, 0),
            "end_datetime": at(31, 0),
        },
    )


async def test_an_alert_alone_is_described_but_does_not_count_as_unread(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    aula.notifications = [notification(AulaPostNotification, False, title="Alert")]
    await set_up(hass, freezer)
    assert _attributes(hass, "binary_sensor.aula_unread_post", "total", "alert", "title") == (
        "off",
        {"total": 0, "alert": True, "title": "Alert"},
    )


async def test_updating_an_entity_reads_aula_again(
    hass: HomeAssistant, aula: FakeAula, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)
    aula.message_threads = [thread("New")]
    aula.events[SCHOOL.id] = [event(1, "Trip", at(28, 15), at(28, 16))]
    freezer.move_to(at(28, 12, 1))
    await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {"entity_id": ["binary_sensor.aula_unread_message", "calendar.aula_events_alma_school"]},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _attributes(hass, "binary_sensor.aula_unread_message", "subject") == ("on", {"subject": "New"})
    assert _attributes(hass, "calendar.aula_events_alma_school", "message") == ("off", {"message": "⭐ Trip"})
