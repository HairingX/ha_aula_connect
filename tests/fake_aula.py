"""A made-up Aula account, read through the client's methods."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from typing import Any

from aula_connect import Anomaly
from aula_connect.api.aula_errors import NoSickReportRouteError, SickReportFinalError
from aula_connect.widget_probe import WidgetProbeReport
from aula_connect.api.models.module import (
    AULA_NOTIFICATION_TYPES,
    AulaBirthdayEvent,
    AulaCalendarEvent,
    AulaChildProfile,
    AulaClosedDay,
    AulaDailyOverview,
    AulaEasyiqWeeklyPlan,
    AulaEventDetail,
    AulaInstitutionGroup,
    AulaInstitutionProfile,
    AulaLoginData,
    AulaMessageThread,
    AulaOpeningDay,
    AulaPosts,
    AulaProfile,
    AulaProfilePicture,
    AulaSickReportCheck,
    AulaSickReportRoute,
    AulaVacation,
    AulaVacationDay,
    AulaVacationRequest,
    AulaVacationRequestDetail,
    AulaWeeklyNewsletter,
    AulaWeeklyPlan,
)

SCHOOL = AulaInstitutionProfile(id=11, institution_code="S1", institution_name="School")
PARENT_AT_SCHOOL = AulaInstitutionProfile(id=21, institution_code="S1", institution_name="School")
GROUP = AulaInstitutionGroup(id=1, name="3A", short_name="3A")
CHILD = AulaChildProfile(
    first_name="Alma",
    id=11,
    institution_code="S1",
    institution_profile=SCHOOL,
    main_group=GROUP,
    name="Alma Example",
    profile_id=101,
    short_name="AE",
    user_id="alma",
)
PARENT = AulaProfile(
    children=[CHILD],
    first_name="Pat",
    institution_profiles=[PARENT_AT_SCHOOL],
    name="Pat Example",
    profile_id=201,
    user_id="pat",
)


class FakeAula:
    """An Aula account read through the client's methods, with the data each test gives it."""

    def __init__(self, parent: AulaProfile) -> None:
        self.parent = parent
        self.aula_version = 23
        self.found: list[Anomaly] = []
        self.login_error: Exception | None = None
        self.widgets: set[str] = {"0001", "0004", "0029"}
        self.birthdays: list[AulaBirthdayEvent] = []
        self.events: dict[int, list[AulaCalendarEvent]] = {}
        self.weekly_plans: list[AulaWeeklyPlan] = []
        self.easyiq_plans: list[AulaEasyiqWeeklyPlan] = []
        self.newsletters: list[AulaWeeklyNewsletter] = []
        self.daily_overviews: list[AulaDailyOverview] = []
        self.message_threads: list[AulaMessageThread] = []
        self.notifications: list[AULA_NOTIFICATION_TYPES] = []
        self.event_details: dict[int, AulaEventDetail] = {}
        self.event_error: Exception | None = None
        self.vacation_requests: list[AulaVacationRequest] = []
        self.vacation_request_details: dict[int, AulaVacationRequestDetail] = {}
        self.vacations: list[AulaVacation] = []
        self.posts = AulaPosts([], None, False)
        self.closed_days: list[AulaClosedDay] = []
        self.opening_days: list[AulaOpeningDay] = []
        self.routes: dict[int, AulaSickReportRoute] = {}
        self.threads: list[AulaMessageThread] = []
        self.picture: AulaProfilePicture | None = None
        self.probe: WidgetProbeReport | None = None
        self.probe_reads: tuple[str, ...] | None = None
        """The addresses the latest probe was asked to read."""
        self.error: Exception | None = None
        """What every call below raises, where it is set."""
        self.writes: list[tuple[str, tuple[Any, ...]]] = []
        """Each write asked for, by its method, with its arguments."""
        self.reads: list[str] = []
        """Each of the calls below that only reads, by its method."""
        self.asked: list[int] = []
        """The id of each child and institution profile a read was asked about."""

    def _call(self, method: str, *args: Any, write: bool = True) -> None:
        if write:
            self.writes.append((method, args))
        else:
            self.reads.append(method)
        if self.error is not None:
            raise self.error

    async def get_vacation_requests(self, children: list[AulaChildProfile]) -> list[AulaVacationRequest]:
        self.asked.extend(child.id for child in children)
        self._call("get_vacation_requests", write=False)
        return list(self.vacation_requests)

    async def get_vacation_request(self, response_id: int) -> AulaVacationRequestDetail | None:
        self._call("get_vacation_request", write=False)
        return self.vacation_request_details.get(response_id)

    async def answer_vacation_request(self, response_id: int, days: Sequence[AulaVacationDay], comment: str = "") -> None:
        self._call("answer_vacation_request", response_id, list(days), comment)

    async def get_sick_report_route(self, child: AulaChildProfile) -> AulaSickReportRoute:
        self.asked.append(child.id)
        self._call("get_sick_report_route", write=False)
        return self.routes.get(child.id, AulaSickReportRoute.AULA)

    async def report_sick(self, child: AulaChildProfile) -> AulaSickReportRoute:
        self._call("report_sick", child.id)
        return self._route(child)

    async def check_sick_report(self, child: AulaChildProfile) -> AulaSickReportCheck:
        self._call("check_sick_report", write=False)
        route = self.routes.get(child.id, AulaSickReportRoute.AULA)
        found = route is not AulaSickReportRoute.NONE
        return AulaSickReportCheck(route, found, False, "PUT /x" if found else None, True if found else None)

    async def undo_sick(self, child: AulaChildProfile) -> None:
        self._call("undo_sick", child.id)
        if self._route(child) is AulaSickReportRoute.TABULEX:
            raise SickReportFinalError("IST Tabulex")

    def _route(self, child: AulaChildProfile) -> AulaSickReportRoute:
        route = self.routes.get(child.id, AulaSickReportRoute.AULA)
        if route is AulaSickReportRoute.NONE:
            raise NoSickReportRouteError("none")
        return route

    async def get_posts(self, profiles: list[AulaProfile], limit: int = 20) -> AulaPosts:
        self._call("get_posts", write=False)
        return self.posts

    async def get_closed_days(self, children: list[AulaChildProfile]) -> list[AulaClosedDay]:
        self._call("get_closed_days", write=False)
        return list(self.closed_days)

    async def get_opening_hours(self, children: list[AulaChildProfile], start: date, end: date) -> list[AulaOpeningDay]:
        self._call("get_opening_hours", write=False)
        return [day for day in self.opening_days if start <= day.day <= end]

    async def get_vacations(self, children: list[AulaChildProfile]) -> list[AulaVacation]:
        self._call("get_vacations", write=False)
        return list(self.vacations)

    async def add_vacation(self, children: list[AulaChildProfile], start: date, end: date, comment: str = "") -> None:
        self._call("add_vacation", [child.id for child in children], start, end, comment)

    async def delete_notifications(self, notifications: list[AULA_NOTIFICATION_TYPES]) -> None:
        self._call("delete_notifications", [note.notification_id for note in notifications])
        self.notifications = [note for note in self.notifications if note not in notifications]

    async def delete_vacation(self, vacation_id: int) -> None:
        self._call("delete_vacation", vacation_id)

    async def threads_about(self, child: AulaChildProfile) -> list[AulaMessageThread]:
        self._call("threads_about", write=False)
        return [thread for thread in self.threads if any(about.profile_id == child.profile_id for about in thread.regarding_children)]

    async def reply(self, thread_id: int, text: str) -> None:
        self._call("reply", thread_id, text)

    async def get_profile_picture(self, child: AulaChildProfile) -> AulaProfilePicture | None:
        self.asked.append(child.id)
        self._call("get_profile_picture", write=False)
        return self.picture

    async def probe_widgets(self, session: Any = None, reads: Sequence[str] = ()) -> WidgetProbeReport:
        self._call("probe_widgets", write=False)
        self.probe_reads = tuple(reads)
        assert self.probe is not None
        return self.probe

    @property
    def widget_probe(self) -> WidgetProbeReport | None:
        return self.probe

    def anomalies(self, since: datetime | None = None) -> tuple[Anomaly, ...]:
        return tuple(anomaly for anomaly in self.found if since is None or anomaly.last_seen >= since)

    async def connection_check(self) -> None:
        return None

    async def login(self) -> AulaLoginData:
        if self.login_error is not None:
            raise self.login_error
        return AulaLoginData(api_version=23, profiles=[self.parent], widgets=[])

    def has_widget(self, widget_id: str) -> bool:
        return widget_id in self.widgets

    async def get_daily_overviews(self, profiles: list[AulaProfile]) -> list[AulaDailyOverview]:
        self.asked.extend(child.id for profile in profiles for child in profile.children)
        return list(self.daily_overviews)

    async def get_message_threads(self) -> list[AulaMessageThread]:
        return list(self.message_threads)

    async def get_notifications(self, children: list[AulaChildProfile]) -> list[AULA_NOTIFICATION_TYPES]:
        self.asked.extend(child.id for child in children)
        return list(self.notifications)

    async def get_birthday_events(self, profiles: list[AulaChildProfile], start: datetime, end: datetime) -> list[AulaBirthdayEvent]:
        self.asked.extend(profile.id for profile in profiles)
        return list(self.birthdays)

    async def get_calendar_events(
        self, institutions: list[AulaInstitutionProfile], start: datetime, end: datetime
    ) -> list[AulaCalendarEvent]:
        self.asked.extend(institution.id for institution in institutions)
        return [item for institution in institutions for item in self.events.get(institution.id, [])]

    async def get_event(self, event_id: int) -> AulaEventDetail | None:
        if self.event_error is not None:
            raise self.event_error
        return self.event_details.get(event_id)

    async def get_weekly_plans(self, profiles: list[AulaChildProfile], start: datetime, end: datetime) -> list[AulaWeeklyPlan]:
        self.asked.extend(profile.id for profile in profiles)
        return list(self.weekly_plans)

    async def get_easyiq_weekly_plans(self, profiles: list[AulaChildProfile], start: datetime, end: datetime) -> list[AulaEasyiqWeeklyPlan]:
        self.asked.extend(profile.id for profile in profiles)
        return list(self.easyiq_plans)

    async def get_newsletters(self, profiles: list[AulaChildProfile], start: datetime, end: datetime) -> list[AulaWeeklyNewsletter]:
        self.asked.extend(profile.id for profile in profiles)
        return list(self.newsletters)


def failing_once(read: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """`read`, failing the first time it is called."""
    calls = 0

    async def once(*args: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("down")
        return await read(*args)

    return once
