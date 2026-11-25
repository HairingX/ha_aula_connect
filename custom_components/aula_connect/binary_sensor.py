"""The binary sensors of an Aula login: unread messages, media, calendar events, posts and presence,
and for each child whether it has school today and tomorrow, and a vacation request to answer."""

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from typing import Any, List
import logging

from .entity import AulaChildEventsEntity, AulaEntityBase, async_add_child_entities, child_unique_id
from .aula_calendar_coordinator import AulaCalendarCoordinator
from .aula_data_coordinator import MESSAGE_THREADS, NOTIFICATIONS, VACATION_REQUESTS, AulaDataCoordinator, AulaDataCoordinatorData, pending_vacation_requests
from .aula_data import AulaConfigEntry
from .school_day import school_day
from aula_connect.api.module import AulaChildProfile, AulaMessageThread, AulaAlbumNotification, AulaCalendarEventNotification, AulaGalleryNotification, AulaPostNotification, AulaPresenceNotification, NotificationType

_LOGGER = logging.getLogger(__name__)

# The entities read what the coordinators fetched, so updating them never waits on Aula.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: AulaConfigEntry, async_add_entities: AddEntitiesCallback):
    """Add the login's unread message, gallery, calendar, post and presence binary sensors, and each child's school day and vacation request."""
    coordinator = entry.runtime_data.data_coordinator
    entities: List[BinarySensorEntity] = []
    entities.append(AulaUnreadMessageBinarySensor(coordinator))
    entities.append(AulaUnreadGalleryBinarySensor(coordinator))
    entities.append(AulaUnreadCalendarEventBinarySensor(coordinator))
    entities.append(AulaUnreadPostBinarySensor(coordinator))
    entities.append(AulaUnreadPresenceBinarySensor(coordinator))
    async_add_entities(entities)
    calendar_coordinator = entry.runtime_data.calendar_coordinator

    def create(child: AulaChildProfile) -> List[BinarySensorEntity]:
        return [
            AulaSchoolDayBinarySensor(calendar_coordinator, child, days_ahead=0),
            AulaSchoolDayBinarySensor(calendar_coordinator, child, days_ahead=1),
            AulaVacationRequestBinarySensor(coordinator, child),
        ]

    async_add_child_entities(entry, async_add_entities, create)


class AulaSchoolDayBinarySensor(AulaChildEventsEntity, BinarySensorEntity):
    """Whether a child has lessons today, or tomorrow, and when they start and end; a holiday counts as none."""

    def __init__(self, coordinator: AulaCalendarCoordinator, child: AulaChildProfile, days_ahead: int) -> None:
        super().__init__(coordinator, "school_tomorrow" if days_ahead else "school_today", child)
        self._days_ahead = days_ahead

    def _set_values(self) -> None:
        day = school_day(self.coordinator, self._child, dt_util.now().date() + timedelta(days=self._days_ahead))
        self._attr_is_on = None if day is None else day.is_school_day
        self._attr_extra_state_attributes = {
            "start": None if day is None else day.start,
            "end": None if day is None else day.end,
        }


class AulaVacationRequestBinarySensor(AulaEntityBase[AulaChildProfile], BinarySensorEntity):
    """Whether a child's institution waits for an answer to a vacation request, with how many and the first deadline."""

    _reads = VACATION_REQUESTS

    def __init__(self, coordinator: AulaDataCoordinator, child: AulaChildProfile) -> None:
        super().__init__(coordinator, name="vacation_request", context=child)
        self._attr_unique_id = child_unique_id(coordinator, "vacation_request", child)
        self._init_data()

    def _set_values(self, data: AulaDataCoordinatorData, context: AulaChildProfile) -> None:
        pending = pending_vacation_requests(data, context)
        deadlines = [request.response_deadline for request in pending if request.response_deadline is not None]
        self._attr_is_on = bool(pending)
        self._attr_extra_state_attributes = {
            "count": len(pending),
            "deadline": min(deadlines) if deadlines else None,
        }


class AulaUnreadGalleryBinarySensor(AulaEntityBase[None], BinarySensorEntity):
    """Whether Aula has notified of new pictures or albums, with how many."""

    _reads = NOTIFICATIONS

    def __init__(self, coordinator: AulaDataCoordinator):
        super().__init__(coordinator, name="unread_gallery", context=None)
        self._init_data()

    def _set_values(self, data: AulaDataCoordinatorData, context:None) -> None:
        self._attr_is_on = False
        notifications = data.notifications
        total = 0
        for notification in notifications:
            if  isinstance(notification, AulaGalleryNotification):
                total += len(notification.media_ids)
            elif isinstance(notification, AulaAlbumNotification):
                total += 1

        self._attr_is_on = total > 0
        self._attr_icon = 'mdi:image-album'
        attributes = dict[str, Any]()
        attributes["total"] = total
        self._attr_extra_state_attributes = attributes

class AulaUnreadCalendarEventBinarySensor(AulaEntityBase[None], BinarySensorEntity):
    """Whether Aula has unread calendar event notifications, with the first, an alert before others."""

    _reads = NOTIFICATIONS

    def __init__(self, coordinator: AulaDataCoordinator):
        super().__init__(coordinator, name="unread_calendar", context=None)
        self._init_data()

    def _set_values(self, data: AulaDataCoordinatorData, context:None) -> None:
        self._attr_is_on = False
        notifications = data.notifications
        total = 0
        first: AulaCalendarEventNotification|None = None
        for notification in notifications:
            if  isinstance(notification, AulaCalendarEventNotification):
                if notification.notification_type == NotificationType.BADGE:
                    total += 1
                if not first:
                    first = notification
                elif first.notification_type == NotificationType.BADGE and notification.notification_type == NotificationType.ALERT:
                    first = notification

        self._attr_is_on = total > 0
        self._attr_icon = 'mdi:calendar-badge'
        attributes = dict[str, Any]()
        attributes["total"] = total
        attributes["alert"] = False
        attributes["all_day"] = None
        attributes["end_datetime"] = None
        attributes["start_datetime"] = None
        attributes["title"] = None
        if first:
            attributes["alert"] = first.notification_type == NotificationType.ALERT
            attributes["all_day"] = first.is_all_day_event
            attributes["end_datetime"] = first.end_datetime
            attributes["start_datetime"] = first.start_datetime
            attributes["title"] = first.title
        self._attr_extra_state_attributes = attributes

class AulaUnreadMessageBinarySensor(AulaEntityBase[None], BinarySensorEntity):
    """Whether the login has unread message threads, with the first, an unmuted one before others."""

    _reads = MESSAGE_THREADS

    def __init__(self, coordinator: AulaDataCoordinator):
        super().__init__(coordinator, name="unread_message", context=None)
        self._init_data()

    def _set_values(self, data: AulaDataCoordinatorData, context:None) -> None:
        self._attr_is_on = False
        threads = data.message_threads
        first: AulaMessageThread|None = None
        total = 0
        for thread in threads:
            if not thread.read:
                total += 1
                if not first: first = thread
                elif first.muted and not thread.muted: first = thread

        self._attr_is_on = first is not None
        self._attr_icon = 'mdi:message-badge'
        attributes = dict[str, Any]()
        attributes["total"] = total
        attributes["subject"] = None
        attributes["sensitive"] = False
        attributes["muted"] = False
        attributes["marked"] = False
        attributes["recipients"] = None
        attributes["timestamp"] = None
        attributes["text"] = None
        if first is not None:
            attributes["subject"] = first.subject
            attributes["sensitive"] = first.sensitive
            attributes["muted"] = first.muted
            attributes["marked"] = first.marked
            recipients = [rec.answer_directly_name for rec in first.recipients]
            if first.extra_recipients_count > 0:
                recipients.append(f"+{first.extra_recipients_count}")
            attributes["recipients"] = ", ".join(recipients)
            latestmsg = first.latest_message
            if latestmsg is not None:
                attributes["timestamp"] = latestmsg.send_datetime
                text = latestmsg.text
                html = None if text is None else text.html
                attributes["text"] = html
            # Aula gives a sensitive message no subject and no text.
            if first.sensitive:

                attributes["subject"] = "Sensitive"
                attributes["text"] = "This message is sensitive. Please log into Aula to read it."
        self._attr_extra_state_attributes = attributes

class AulaUnreadPostBinarySensor(AulaEntityBase[None], BinarySensorEntity):
    """Whether Aula has unread post notifications, with the first, an alert before others."""

    _reads = NOTIFICATIONS

    def __init__(self, coordinator: AulaDataCoordinator):
        super().__init__(coordinator, name="unread_post", context=None)
        self._init_data()

    def _set_values(self, data: AulaDataCoordinatorData, context:None) -> None:
        self._attr_is_on = False
        notifications = data.notifications
        total = 0
        first: AulaPostNotification|None = None
        for notification in notifications:
            if  isinstance(notification, AulaPostNotification):
                if notification.notification_type == NotificationType.BADGE:
                    total += 1
                if not first:
                    first = notification
                elif first.notification_type == NotificationType.BADGE and notification.notification_type == NotificationType.ALERT:
                    first = notification

        self._attr_is_on = total > 0
        self._attr_icon = 'mdi:bulletin-board'
        attributes = dict[str, Any]()
        attributes["total"] = total
        attributes["alert"] = False
        attributes["title"] = None
        if first:
            attributes["alert"] = first.notification_type == NotificationType.ALERT
            attributes["title"] = first.title
        self._attr_extra_state_attributes = attributes

class AulaUnreadPresenceBinarySensor(AulaEntityBase[None], BinarySensorEntity):
    """Whether Aula has unread presence notifications, with the first, an alert before others."""

    _reads = NOTIFICATIONS

    def __init__(self, coordinator: AulaDataCoordinator):
        super().__init__(coordinator, name="unread_presence", context=None)
        self._init_data()

    def _set_values(self, data: AulaDataCoordinatorData, context:None) -> None:
        self._attr_is_on = False
        notifications = data.notifications
        total = 0
        first: AulaPresenceNotification|None = None
        for notification in notifications:
            if  isinstance(notification, AulaPresenceNotification):
                if notification.notification_type == NotificationType.BADGE:
                    total += 1
                if not first:
                    first = notification
                elif first.notification_type == NotificationType.BADGE and notification.notification_type == NotificationType.ALERT:
                    first = notification

        self._attr_is_on = total > 0
        self._attr_icon = 'mdi:calendar-multiselect'
        attributes = dict[str, Any]()
        attributes["total"] = total
        attributes["alert"] = False
        attributes["title"] = None
        attributes["text"] = None
        attributes["start_datetime"] = None
        attributes["end_datetime"] = None
        if first:
            attributes["alert"] = first.notification_type == NotificationType.ALERT
            attributes["title"] = first.vacation_request_name
            attributes["text"] = first.message_text
            attributes["start_datetime"] = first.start_datetime
            attributes["end_datetime"] = first.end_datetime
        self._attr_extra_state_attributes = attributes