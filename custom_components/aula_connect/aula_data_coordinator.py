"""The coordinator that reads an Aula login's profiles, daily overviews, messages and notifications."""

from dataclasses import dataclass
from datetime import timedelta
from typing import List
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .redact import error_text
from .const import ANOMALY_WINDOW, DOMAIN, anomaly_issue_id
from .retry import read_twice
import asyncio
from collections.abc import Awaitable, Callable
import logging

from aula_connect.api.models.constants import AulaWidgetId
from aula_connect.api.models.module import (
    AulaChildProfile,
    AulaDailyOverview,
    AulaMessageThread,
    AulaProfile,
    AULA_NOTIFICATION_TYPES
)
from aula_connect import AnomalyKind
from aula_connect.client import AulaClient
from aula_connect.api.aula_errors import AulaCredentialError

_LOGGER = logging.getLogger(__name__)

DATA_COORDINATOR_POLL_INTERVAL = timedelta(minutes=5)

UPDATE_TIMEOUT = timedelta(minutes=4)
"""How long one refresh may take, each part read twice at the most."""

DAILY_OVERVIEWS = "daily overviews"
MESSAGE_THREADS = "message threads"
NOTIFICATIONS = "notifications"

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
    unreadable: frozenset[str] = frozenset()
    """The parts that failed to read twice in a row; each keeps what was read of it before."""

MAX_DETAILS = 10
"""How many details a repair issue names before it says how many more there are."""


def _listed(details: list[str]) -> str:
    shown = ", ".join(details[:MAX_DETAILS])
    return shown if len(details) <= MAX_DETAILS else f"{shown} +{len(details) - MAX_DETAILS}"


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
        self._partly_unreadable = False
        self.device_id = device_id
        self._entry_id = config_entry.entry_id
        self._watching_since = dt_util.utcnow()
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
            async with asyncio.timeout(UPDATE_TIMEOUT.total_seconds()):
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
        finally:
            self._report_anomalies()

    def _report_anomalies(self) -> None:
        """Ask for the diagnostics, one issue a kind, while Aula sends what is not known.

        An issue goes once nothing of its kind was met within the window; an issue left from
        before the client was made stays until the client has watched a whole window, as deleting
        it would forget that the user ignored it.
        """
        now = dt_util.utcnow()
        found: dict[AnomalyKind, list[str]] = {}
        for anomaly in self._client.anomalies(since=now - ANOMALY_WINDOW):
            found.setdefault(anomaly.kind, []).append(anomaly.detail)
        watched_a_window = now - self._watching_since >= ANOMALY_WINDOW
        for kind in AnomalyKind:
            issue = anomaly_issue_id(kind, self._entry_id)
            details = found.get(kind)
            if details is None:
                if watched_a_window:
                    ir.async_delete_issue(self.hass, DOMAIN, issue)
                continue
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=kind.value,
                translation_placeholders={"details": _listed(sorted(details))},
                learn_more_url="https://github.com/HairingX/ha_aula_connect#troubleshooting",
            )

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
            logindata = await read_twice("the login", self._client.login)
            profiles = logindata.profiles
            self.aula_version = logindata.api_version
        except AulaCredentialError:
            raise
        except Exception as ex:
            return ex

        children = [child for profile in profiles for child in profile.children]
        before = self.data
        failures = dict[str, str]()

        async def part[T](what: str, read: Callable[[], Awaitable[List[T]]], kept: List[T], complete: Callable[[List[T]], bool] = lambda _: True) -> List[T]:
            try:
                return await read_twice(what, read, complete)
            except AulaCredentialError:
                raise
            except Exception as ex:
                failures[what] = error_text(ex)
                return kept

        def every_child(overviews: List[AulaDailyOverview]) -> bool:
            present = {overview.institution_profile.id for overview in overviews}
            return all(child.id in present for child in children)

        daily_overviews = await part(
            DAILY_OVERVIEWS, lambda: self._client.get_daily_overviews(profiles), before.daily_overviews if before else [], every_child
        )
        message_threads = await part(MESSAGE_THREADS, self._client.get_message_threads, before.message_threads if before else [])
        notifications = await part(
            NOTIFICATIONS, lambda: self._client.get_notifications(children), before.notifications if before else []
        )

        if failures:
            # Logged once when Aula can be read only in part, and once when it can be read in full.
            if not self._partly_unreadable:
                self._partly_unreadable = True
                _LOGGER.warning("Aula could be read only in part, twice in a row: %s", "; ".join(f"{what}: {error}" for what, error in failures.items()))
        elif self._partly_unreadable:
            self._partly_unreadable = False
            _LOGGER.info("Aula can be read in full again")

        return AulaDataCoordinatorData(
            aula_version=self.aula_version,
            children=children,
            daily_overviews=daily_overviews,
            device_id=self.device_id,
            message_threads=message_threads,
            notifications=notifications,
            unreadable=frozenset(failures),
            profiles=profiles,
        )