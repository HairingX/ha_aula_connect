"""The coordinator that reads an Aula login's profiles, daily overviews, messages and notifications."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import List
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .devices import async_tidy_child_devices, disabled_children
from .family import FamilyMemory
from .redact import error_text
from .const import ANOMALY_HELP, ANOMALY_WINDOW, DOMAIN, anomaly_issue_id
from .retry import read_twice
import asyncio
from collections.abc import Awaitable, Callable
import logging

from aula_connect.api.models.constants import AulaWidgetId
from aula_connect.api.models.module import (
    AulaChildProfile,
    AulaDailyOverview,
    AulaMessageThread,
    AulaPresenceStatus,
    AulaProfile,
    AulaSickReportRoute,
    AulaVacationRequest,
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
VACATION_REQUESTS = "vacation requests"
SICK_ROUTES = "sick report routes"

SICK_ROUTES_INTERVAL = timedelta(hours=1)
"""How often the way each child is reported sick is read again; it changes only with the institution's settings."""

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
    vacation_requests: List[AulaVacationRequest] = field(default_factory=lambda: list[AulaVacationRequest]())
    sick_routes: dict[int, AulaSickReportRoute] = field(default_factory=lambda: dict[int, AulaSickReportRoute]())
    """The way each child is reported sick, by its id; a child not read yet is missing."""
    listed_children: List[AulaChildProfile] = field(default_factory=lambda: list[AulaChildProfile]())
    """Every child Aula lists for the login, those whose device is disabled included."""

def pending_vacation_requests(data: AulaDataCoordinatorData, child: AulaChildProfile) -> List[AulaVacationRequest]:
    """The child's vacation requests that wait for an answer and can still be answered today."""
    today = dt_util.now().date()
    return [
        request for request in data.vacation_requests
        if request.child_id == child.id and request.missing_answer and request.answerable(today)
    ]


MAX_DETAILS = 10
"""How many details a repair issue names before it says how many more there are."""


def _listed(details: list[str]) -> str:
    shown = ", ".join(details[:MAX_DETAILS])
    return shown if len(details) <= MAX_DETAILS else f"{shown} +{len(details) - MAX_DETAILS}"


class AulaDataCoordinator(DataUpdateCoordinator[AulaDataCoordinatorData]):
    """Reads the login's profiles, daily overviews, message threads and notifications from Aula."""

    _client: AulaClient

    def __init__(
        self, device_id: str, hass: HomeAssistant, client: AulaClient, config_entry: ConfigEntry, memory: FamilyMemory
    ):
        super().__init__(
            hass,
            _LOGGER,
            name="general",
            config_entry=config_entry,
            update_interval=DATA_COORDINATOR_POLL_INTERVAL,
            always_update=True,
        )
        self._client = client
        self._memory = memory
        self._partly_unreadable = False
        self._routes_read: datetime | None = None
        self._own_replies = set[int]()
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
                learn_more_url=ANOMALY_HELP[kind],
            )

    def note_own_reply(self, thread_id: int) -> None:
        """Remember that Home Assistant replied in the thread, so that its reply is not taken for another's."""
        self._own_replies.add(thread_id)

    def own_reply_read(self, thread_id: int) -> bool:
        """Whether Home Assistant replied in the thread since it was last asked; asking forgets it."""
        if thread_id in self._own_replies:
            self._own_replies.discard(thread_id)
            return True
        return False

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

        listed = [child for profile in profiles for child in profile.children]
        async_tidy_child_devices(self.hass, self._entry_id, listed)
        # A child whose device is disabled is left out before anything is asked of Aula for it.
        left_out = disabled_children(self.hass, self._entry_id, listed)
        profiles = [
            replace(profile, children=[child for child in profile.children if child.id not in left_out])
            for profile in profiles
        ]
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
        vacation_requests = await part(
            VACATION_REQUESTS, lambda: self._client.get_vacation_requests(children), before.vacation_requests if before else []
        )
        sick_routes = await self._sick_routes(children, before.sick_routes if before else {}, failures)

        today = dt_util.now().date()
        for overview in daily_overviews:
            if overview.status == AulaPresenceStatus.SICK and any(child.id == overview.institution_profile.id for child in children):
                self._memory.note_sick(overview.institution_profile.id, today)

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
            vacation_requests=vacation_requests,
            sick_routes=sick_routes,
            listed_children=listed,
        )

    async def _sick_routes(
        self, children: List[AulaChildProfile], kept: dict[int, AulaSickReportRoute], failures: dict[str, str]
    ) -> dict[int, AulaSickReportRoute]:
        """The way each child is reported sick, read again once SICK_ROUTES_INTERVAL has passed."""
        now = dt_util.utcnow()
        if self._routes_read is not None and now - self._routes_read < SICK_ROUTES_INTERVAL and all(
            child.id in kept for child in children
        ):
            return kept

        async def read() -> List[tuple[int, AulaSickReportRoute]]:
            return [(child.id, await self._client.get_sick_report_route(child)) for child in children]

        try:
            routes = dict(await read_twice(SICK_ROUTES, read))
        except AulaCredentialError:
            raise
        except Exception as ex:
            failures[SICK_ROUTES] = error_text(ex)
            return kept
        self._routes_read = now
        return routes