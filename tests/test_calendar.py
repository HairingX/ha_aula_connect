"""Tests of the calendars' handling of dates and times."""

from __future__ import annotations

from datetime import UTC, date, datetime

from custom_components.aula_connect import calendar


def test_a_date_becomes_its_midnight_in_the_time_zone_given() -> None:
    assert calendar.to_datetime(date(2026, 10, 28), UTC) == datetime(2026, 10, 28, tzinfo=UTC)


def test_a_datetime_stays_as_it_is() -> None:
    moment = datetime(2026, 10, 28, 8, 15, tzinfo=UTC)
    assert calendar.to_datetime(moment, None) is moment
