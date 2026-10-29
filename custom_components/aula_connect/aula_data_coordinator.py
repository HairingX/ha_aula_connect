"""The coordinator that reads an Aula login's profiles, daily overviews, messages and notifications."""

from dataclasses import dataclass
from datetime import timedelta
from typing import List
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .redact import error_text
from .const import DOMAIN
import asyncio
import logging

from aula_connect.api.models.constants import AulaWidgetId
from aula_connect.api.models.module import (
    AulaChildProfile,
    AulaDailyOverview,
    AulaMessageThread,
    AulaProfile,
    AULA_NOTIFICATION_TYPES
)
from aula_connect.client import AulaClient
from aula_connect.api.aula_errors import AulaCredentialError

_LOGGER = logging.getLogger(__name__)

DATA_COORDINATOR_POLL_INTERVAL = timedelta(minutes=5)

MAX_CONSECUTIVE_FAILURES = 3
"""How many reads in a row may fail in part before the coordinator reports a failure."""

@dataclass
class AulaDataCoordinatorData:
    """What one read of Aula found for the login."""

    device_id: str
    aula_version: int
    profiles: List[AulaProfile]
    children: List[AulaChildProfile]
    daily_overviews: List[AulaDailyOverview]
    message_threads: List[AulaMessageThread]
    notifications: List[AULA_NOTIFICATION_TYPES]

class AulaDataCoordinator(DataUpdateCoordinator[AulaDataCoordinatorData]):
    """Reads the login's profiles, daily overviews, message threads and notifications from Aula."""

    _client: AulaClient

    def __init__(self, device_id: str, hass: HomeAssistant, client: AulaClient, config_entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            name="general",
            config_entry=config_entry,
            update_interval=DATA_COORDINATOR_POLL_INTERVAL,
            always_update=True,
        )
        self._client = client
        self._consecutive_failures = 0
        self._partly_unreadable = False
        self.device_id = device_id
        self.aula_version = client.aula_version

        _LOGGER.debug(f"Coordinator {device_id} initialized")

    async def _async_setup(self) -> None:
        """Check that Aula can be reached with the entry's login."""
        try:
            await self._client.connection_check()
        except AulaCredentialError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="login_expired") from err
        except Exception as err:
            # Not chained: the error's own text holds the access token, which a traceback would log.
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cannot_connect", translation_placeholders={"error": error_text(err)}
            ) from None

    async def _async_update_data(self) -> AulaDataCoordinatorData:
        """Read the login's data from Aula, a part it cannot read left empty."""
        try:
            async with asyncio.timeout(120):
                data = await self._fetch_data()
                if isinstance(data, Exception):
                    raise data
                return data
        except AulaCredentialError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="login_expired") from err
        except Exception as err:
            # Not chained: the error's own text holds the access token, which a traceback would log.
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cannot_connect", translation_placeholders={"error": error_text(err)}
            ) from None

    def weekly_plans_supported(self) -> bool:
        """Whether the login's institutions offer weekly plans."""
        return self._client.has_widget(AulaWidgetId.WEEKPLAN_PARENTS)

    def easyiq_weekplan_supported(self) -> bool:
        """Whether the login's institutions offer EasyIQ weekly plans."""
        return self._client.has_widget(AulaWidgetId.EASYIQ_WEEKPLAN)

    def reminders_supported(self) -> bool:
        """Whether the login's institutions offer reminders."""
        return self._client.has_widget(AulaWidgetId.REMINDERS)

    def newsletter_supported(self) -> bool:
        """Whether the login's institutions offer MinUddannelse newsletters."""
        return self._client.has_widget(AulaWidgetId.MY_EDUCATION_WEEKLETTER)

    def assignments_supported(self) -> bool:
        """Whether the login's institutions offer MinUddannelse assignments."""
        return self._client.has_widget(AulaWidgetId.MY_EDUCATION_ASSIGNMENTS)

    async def _fetch_data(self) -> AulaDataCoordinatorData|Exception:
        try:

            logindata = await self._client.login()
            profiles = logindata.profiles
            self.aula_version = logindata.api_version
        except AulaCredentialError:
            raise
        except Exception as ex:
            return ex

        children = [child for profile in profiles for child in profile.children]
        had_failure = False
        failures = list[str]()

        daily_overviews = list[AulaDailyOverview]()
        try:
            daily_overviews = await self._client.get_daily_overviews(profiles)
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"daily overviews: {error_text(ex)}")

        message_threads = list[AulaMessageThread]()
        try:
            message_threads = await self._client.get_message_threads()
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"message threads: {error_text(ex)}")

        notifications = list[AULA_NOTIFICATION_TYPES]()
        try:
            notifications = await self._client.get_notifications(children)
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"notifications: {error_text(ex)}")

        if had_failure:
            # Logged once when Aula can be read only in part, and once when it can be read in full.
            if not self._partly_unreadable:
                self._partly_unreadable = True
                _LOGGER.warning("Aula could be read only in part: %s", "; ".join(failures))
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self._consecutive_failures = 0
                return Exception(f"Aula API failed {MAX_CONSECUTIVE_FAILURES} consecutive times")
        else:
            if self._partly_unreadable:
                self._partly_unreadable = False
                _LOGGER.info("Aula can be read in full again")
            self._consecutive_failures = 0

        return AulaDataCoordinatorData(
            aula_version=self.aula_version,
            children=children,
            daily_overviews=daily_overviews,
            device_id=self.device_id,
            message_threads=message_threads,
            notifications=notifications,
            profiles=profiles,
        )