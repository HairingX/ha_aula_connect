"""A child's school day, from the lessons and holidays of its institution's calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from aula_connect.api.models.constants import AulaCalendarEventType
from aula_connect.api.models.module import AulaCalendarEvent, AulaChildProfile
from homeassistant.util import dt as dt_util

from .aula_calendar_coordinator import AulaCalendarCoordinator
from .calendar import shown_for

HOLIDAYS = (AulaCalendarEventType.HOLIDAY, AulaCalendarEventType.PRESENCE_HOLIDAY)


@dataclass(frozen=True)
class SchoolDay:
    """When a child's lessons on one day start and end; both None on a day off."""

    start: datetime | None = None
    end: datetime | None = None

    @property
    def is_school_day(self) -> bool:
        """Whether the child has lessons that day."""
        return self.start is not None


def _on(event: AulaCalendarEvent, day: date) -> bool:
    """Whether the event takes place on the local day `day`."""
    first = dt_util.start_of_local_day(day)
    after = dt_util.start_of_local_day(day + timedelta(days=1))
    return event.start_datetime < after and event.end_datetime > first


def school_day(coordinator: AulaCalendarCoordinator, child: AulaChildProfile, day: date) -> SchoolDay | None:
    """The child's lessons on `day`; None where the calendar was not read for that day.

    A day with a holiday is a day off, whatever lessons it has.
    """
    institution = child.institution_profile
    read = coordinator.events_read_for(institution)
    if read is None or not read[0] <= day < read[1]:
        return None
    events = [event for event in coordinator.get_events(institution) if _on(event, day) and shown_for(child, institution, event)]
    lessons = [event for event in events if event.type == AulaCalendarEventType.LESSON]
    if not lessons or any(event.type in HOLIDAYS for event in events):
        return SchoolDay()
    return SchoolDay(min(lesson.start_datetime for lesson in lessons), max(lesson.end_datetime for lesson in lessons))
