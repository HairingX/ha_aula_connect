"""Each child's profile picture in Aula, as an image entity."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

import aiohttp
from aula_connect.api.models.module import AulaChildProfile
from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .aula_data import AulaConfigEntry
from .aula_data_coordinator import AulaDataCoordinator
from .entity import async_add_child_entities, child_device_info, child_unique_id
from .redact import error_text

_LOGGER = logging.getLogger(__name__)

# The entities read what the coordinators fetched; a picture is read only when it is asked for.
PARALLEL_UPDATES = 0

CACHED_FOR = timedelta(minutes=10)
"""How long a picture read from Aula is shown before it is read again."""

DOWNLOAD_TIMEOUT = timedelta(seconds=10)


async def async_setup_entry(hass: HomeAssistant, entry: AulaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add each child's picture."""
    async_add_child_entities(entry, async_add_entities, lambda child: [AulaPictureImage(hass, entry, child)])


def picture_key(coordinator: AulaDataCoordinator, child: AulaChildProfile) -> str | None:
    """What tells the child's picture apart from another, as the coordinator last read it; None without one."""
    for found in coordinator.data.children:
        if found.id == child.id and found.profile_picture is not None:
            picture = found.profile_picture
            return f"{picture.id}:{picture.key}"
    return None


class AulaPictureImage(ImageEntity):
    """A child's profile picture, read from Aula anew when it is shown, as Aula's link to it expires.

    The link itself is a credential of sorts, signed for whoever holds it: it is never an attribute
    nor logged.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "picture"

    def __init__(self, hass: HomeAssistant, entry: AulaConfigEntry, child: AulaChildProfile) -> None:
        super().__init__(hass)
        self._entry = entry
        self._child = child
        coordinator = entry.runtime_data.data_coordinator
        self._attr_unique_id = child_unique_id(coordinator, "picture", child)
        self._attr_device_info = child_device_info(coordinator, child)
        self._attr_extra_state_attributes = {"child_id": child.id}
        self._key = picture_key(coordinator, child)
        self._attr_image_last_updated = dt_util.utcnow() if self._key is not None else None
        self._picture: bytes | None = None
        self._read_at = dt_util.utcnow()
        self._lock = asyncio.Lock()

    async def async_added_to_hass(self) -> None:
        """Follow the coordinator's reads, which tell when the child's picture is another."""
        await super().async_added_to_hass()
        coordinator = self._entry.runtime_data.data_coordinator
        self.async_on_remove(coordinator.async_add_listener(self._handle_coordinator_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        key = picture_key(self._entry.runtime_data.data_coordinator, self._child)
        if key != self._key:
            self._key = key
            self._picture = None
            self._attr_image_last_updated = dt_util.utcnow() if key is not None else None
            self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """The picture, read from Aula where the one read is older than CACHED_FOR; None where there is none."""
        async with self._lock:
            if self._picture is not None and dt_util.utcnow() - self._read_at < CACHED_FOR:
                return self._picture
            try:
                picture = await self._entry.runtime_data.client.get_profile_picture(self._child)
                if picture is None or not picture.url:
                    return None
                session = async_get_clientsession(self.hass)
                async with session.get(picture.url, timeout=aiohttp.ClientTimeout(DOWNLOAD_TIMEOUT.total_seconds())) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "")
                    content = await response.read()
            except Exception as err:
                # The error of a download names its signed link; only its kind is logged.
                text = type(err).__name__ if isinstance(err, aiohttp.ClientError) else error_text(err)
                _LOGGER.warning("The picture of %s could not be read: %s", self.entity_id, text)
                return self._picture
            if content_type.startswith("image/"):
                self._attr_content_type = content_type
            self._picture = content
            self._read_at = dt_util.utcnow()
            return content
