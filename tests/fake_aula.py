"""A made-up Aula account, read through the client's methods."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aula_connect.api.models.module import (
    AulaBirthdayEvent,
    AulaCalendarEvent,
    AulaChildProfile,
    AulaEasyiqWeeklyPlan,
    AulaInstitutionGroup,
    AulaInstitutionProfile,
    AulaLoginData,
    AulaProfile,
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
        self.widgets: set[str] = {"0001", "0004", "0029"}
        self.birthdays: list[AulaBirthdayEvent] = []
        self.events: dict[int, list[AulaCalendarEvent]] = {}
        self.weekly_plans: list[AulaWeeklyPlan] = []
        self.easyiq_plans: list[AulaEasyiqWeeklyPlan] = []
        self.newsletters: list[AulaWeeklyNewsletter] = []

    def connection_check(self) -> None:
        return None

    def login(self) -> AulaLoginData:
        return AulaLoginData(api_version=23, profiles=[self.parent], widgets=[])

    def has_widget(self, widget_id: str) -> bool:
        return widget_id in self.widgets

    def get_daily_overviews(self, profiles: Any) -> list[Any]:
        return []

    def get_message_threads(self) -> list[Any]:
        return []

    def get_notifications(self, children: Any) -> list[Any]:
        return []

    def get_birthday_events(self, profiles: Any, start: datetime, end: datetime) -> list[AulaBirthdayEvent]:
        return list(self.birthdays)

    def get_calendar_events(
        self, institutions: list[AulaInstitutionProfile], start: datetime, end: datetime
    ) -> list[AulaCalendarEvent]:
        return [item for institution in institutions for item in self.events.get(institution.id, [])]

    def get_weekly_plans(self, profiles: Any, start: datetime, end: datetime) -> list[AulaWeeklyPlan]:
        return list(self.weekly_plans)

    def get_easyiq_weekly_plans(self, profiles: Any, start: datetime, end: datetime) -> list[AulaEasyiqWeeklyPlan]:
        return list(self.easyiq_plans)

    def get_newsletters(self, profiles: Any, start: datetime, end: datetime) -> list[AulaWeeklyNewsletter]:
        return list(self.newsletters)

    def close(self) -> None:
        return None
