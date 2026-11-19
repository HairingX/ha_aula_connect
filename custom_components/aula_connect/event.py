"""Events of an Aula login: a new message, new photos and a new calendar event, found by comparing
each read of Aula with the one before it."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from aula_connect.api.module import AulaAlbumNotification, AulaCalendarEventNotification, AulaGalleryNotification
from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .aula_data import AulaConfigEntry
from .aula_data_coordinator import MESSAGE_THREADS, NOTIFICATIONS, AulaDataCoordinator, AulaDataCoordinatorData
from .entity import device_info, unique_id

# The entities read what the coordinators fetched, so updating them never waits on Aula.
PARALLEL_UPDATES = 0

NEW_MESSAGE = "new_message"
NEW_PHOTO = "new_photo"
NEW_EVENT = "new_event"


async def async_setup_entry(hass: HomeAssistant, entry: AulaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add the login's events of new messages, photos and calendar events."""
    coordinator = entry.runtime_data.data_coordinator
    async_add_entities([AulaNewMessageEvent(coordinator), AulaNewPhotoEvent(coordinator), AulaNewCalendarEvent(coordinator)])


class _AulaNewEvent(EventEntity):
    """Fires for each item a read of Aula finds that the read before it did not have.

    What Aula holds when the entity is added counts as known, so a restart fires nothing.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _reads: str

    def __init__(self, coordinator: AulaDataCoordinator, key: str) -> None:
        self.coordinator = coordinator
        self._attr_translation_key = key
        self._attr_event_types = [key]
        self._attr_unique_id = unique_id(coordinator, key)
        self._attr_device_info = device_info(coordinator)
        self._known = self._found(coordinator.data)

    async def async_added_to_hass(self) -> None:
        """Follow the coordinator's reads while the entity is in Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        self._attr_available = self.coordinator.last_update_success and self._reads not in data.unreadable
        new = list[dict[str, Any]]()
        if self._attr_available:
            found = self._found(data)
            new = [attributes for key, attributes in found.items() if key not in self._known and self._told(attributes)]
            self._known = found
        for attributes in new:
            self._trigger_event(self._attr_event_types[0], attributes)
            self.async_write_ha_state()
        if not new:
            self.async_write_ha_state()

    def _told(self, attributes: dict[str, Any]) -> bool:
        """Whether a new item is told as an event."""
        return True

    @abstractmethod
    def _found(self, data: AulaDataCoordinatorData) -> dict[str, dict[str, Any]]:
        """Each item of the read that is told as new once, by a key that changes when it is new, with what is told of it."""


class AulaNewMessageEvent(_AulaNewEvent):
    """A new message in an unread thread: its thread's id and subject, but no text nor sender."""

    _reads = MESSAGE_THREADS

    def __init__(self, coordinator: AulaDataCoordinator) -> None:
        super().__init__(coordinator, NEW_MESSAGE)

    def _told(self, attributes: dict[str, Any]) -> bool:
        """Whether the message is someone else's than the one Home Assistant sent in the thread."""
        return not self.coordinator.own_reply_read(attributes["thread_id"])

    def _found(self, data: AulaDataCoordinatorData) -> dict[str, dict[str, Any]]:
        found = dict[str, dict[str, Any]]()
        for thread in data.message_threads:
            latest = thread.latest_message
            if thread.read or latest is None:
                continue
            found[f"{thread.id}:{latest.id}"] = {
                "thread_id": thread.id,
                # Aula gives a sensitive message no subject.
                "subject": None if thread.sensitive else thread.subject,
                "sensitive": thread.sensitive,
                "children": [child.display_name for child in thread.regarding_children],
            }
        return found


class AulaNewPhotoEvent(_AulaNewEvent):
    """New photos or videos in the gallery or an album, as Aula notifies them: the album and how many."""

    _reads = NOTIFICATIONS

    def __init__(self, coordinator: AulaDataCoordinator) -> None:
        super().__init__(coordinator, NEW_PHOTO)

    def _found(self, data: AulaDataCoordinatorData) -> dict[str, dict[str, Any]]:
        found = dict[str, dict[str, Any]]()
        for notification in data.notifications:
            if isinstance(notification, AulaGalleryNotification):
                found[notification.notification_id] = {"album_id": notification.album_id, "count": len(notification.media_ids)}
            elif isinstance(notification, AulaAlbumNotification):
                found[notification.notification_id] = {"album_id": notification.album_id, "count": 1}
        return found


class AulaNewCalendarEvent(_AulaNewEvent):
    """A calendar event Aula notifies, such as an invitation: its id, title and time."""

    _reads = NOTIFICATIONS

    def __init__(self, coordinator: AulaDataCoordinator) -> None:
        super().__init__(coordinator, NEW_EVENT)

    def _found(self, data: AulaDataCoordinatorData) -> dict[str, dict[str, Any]]:
        return {
            notification.notification_id: {
                "event_id": notification.event_id,
                "title": notification.title,
                "start": notification.start_datetime.isoformat(),
                "end": notification.end_datetime.isoformat(),
                "all_day": notification.is_all_day_event,
            }
            for notification in data.notifications
            if isinstance(notification, AulaCalendarEventNotification)
        }
