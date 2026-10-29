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

# Coordinator polling interval
DATA_COORDINATOR_POLL_INTERVAL = timedelta(minutes=5)

# Number of consecutive fetch failures before raising UpdateFailed
MAX_CONSECUTIVE_FAILURES = 3

@dataclass
class AulaDataCoordinatorData:
    device_id: str
    aula_version: int
    profiles: List[AulaProfile]
    children: List[AulaChildProfile]
    daily_overviews: List[AulaDailyOverview]
    message_threads: List[AulaMessageThread]
    notifications: List[AULA_NOTIFICATION_TYPES]

class AulaDataCoordinator(DataUpdateCoordinator[AulaDataCoordinatorData]):
    """My custom coordinator."""

    _client: AulaClient

    def __init__(self, device_id: str, hass: HomeAssistant, client: AulaClient, config_entry: ConfigEntry):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="general",
            config_entry=config_entry,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=DATA_COORDINATOR_POLL_INTERVAL,
            # Set always_update to `False` if the data returned from the
            # api can be compared via `__eq__` to avoid duplicate updates
            # being dispatched to listeners
            always_update=True,
        )
        self._client = client
        self._consecutive_failures = 0
        self._partly_unreadable = False
        self.device_id = device_id
        self.aula_version = client.aula_version

        _LOGGER.debug(f"Coordinator {device_id} initialized")

    async def _async_setup(self) -> None:
        """Set up the coordinator

        This is the place to set up your coordinator,
        or to load data, that only needs to be loaded once.

        This method will be called automatically during
        coordinator.async_config_entry_first_refresh.

        If the refresh fails, async_config_entry_first_refresh will
        raise ConfigEntryNotReady and setup will try again later
        """
        try:
            await self.hass.async_add_executor_job(self._connection_check)
        except AulaCredentialError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="login_expired") from err
        except Exception as err:
            # Not chained: the error's own text holds the access token, which a traceback would log.
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cannot_connect", translation_placeholders={"error": error_text(err)}
            ) from None

    async def _async_update_data(self) -> AulaDataCoordinatorData:
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:

            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with asyncio.timeout(120):
                # Grab active context variables to limit data required to be fetched from API
                # Note: using context is not required if there is no need or ability to limit
                # data retrieved from API.
                # listening_idx = set(self.async_contexts())

                # always retrieve profiles first
                data = await self.hass.async_add_executor_job(self._fetch_data)
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
        return self._client.has_widget(AulaWidgetId.WEEKPLAN_PARENTS)

    def easyiq_weekplan_supported(self) -> bool:
        return self._client.has_widget(AulaWidgetId.EASYIQ_WEEKPLAN)

    def reminders_supported(self) -> bool:
        return self._client.has_widget(AulaWidgetId.REMINDERS)

    def newsletter_supported(self) -> bool:
        return self._client.has_widget(AulaWidgetId.MY_EDUCATION_WEEKLETTER)

    def assignments_supported(self) -> bool:
        return self._client.has_widget(AulaWidgetId.MY_EDUCATION_ASSIGNMENTS)

    def _connection_check(self) -> None:
        self._client.connection_check()

    def _fetch_data(self) -> AulaDataCoordinatorData|Exception:
        # Login is required for all fetches — if this fails, nothing works
        try:
            logindata = self._client.login()
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
            daily_overviews = self._client.get_daily_overviews(profiles)
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"daily overviews: {error_text(ex)}")

        message_threads = list[AulaMessageThread]()
        try:
            message_threads = self._client.get_message_threads()
        except AulaCredentialError:
            raise
        except Exception as ex:
            had_failure = True
            failures.append(f"message threads: {error_text(ex)}")

        notifications = list[AULA_NOTIFICATION_TYPES]()
        try:
            notifications = self._client.get_notifications(children)
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