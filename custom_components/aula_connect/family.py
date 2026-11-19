"""What an entry remembers of a family between restarts: the days each child was sick, the thread
the family writes to its teachers in, the days it wrote there, and the homework ticked at home."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

KEPT = timedelta(days=60)
"""How long a sick day, a day written and a ticked homework are remembered."""

SAVE_DELAY = 1.0
"""Seconds a change waits before it is written, so that several are written at once."""


class RememberedThread(TypedDict):
    """The thread the family last wrote to a child's teachers in, and when it was chosen."""

    id: int
    at: str


class ChildMemory(TypedDict, total=False):
    """What is remembered of one child."""

    sick: list[str]
    wrote: list[str]
    thread: RememberedThread
    done: dict[str, str]
    """The homework ticked, by task id, each with its due day."""


def previous_school_day(day: date) -> date:
    """The weekday before `day`; holidays are not known here, and are counted as school days."""
    before = day - timedelta(days=1)
    while before.weekday() >= 5:
        before -= timedelta(days=1)
    return before


def school_year_start(day: date) -> date:
    """The first of August that starts the school year `day` is in."""
    return date(day.year if day.month >= 8 else day.year - 1, 8, 1)


class FamilyMemory:
    """The memory of one entry, kept in Home Assistant's storage, private to it."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, ChildMemory]] = Store(hass, 1, f"{DOMAIN}.family.{entry_id}", private=True)
        self._children: dict[str, ChildMemory] = {}

    async def async_load(self) -> None:
        """Read what was remembered, forgetting what is older than KEPT."""
        self._children = await self._store.async_load() or {}
        self._prune(dt_util.now().date())

    async def async_save(self) -> None:
        """Write what is remembered now, rather than after SAVE_DELAY."""
        await self._store.async_save(self._children)

    async def async_remove(self) -> None:
        """Forget everything, for good."""
        await self._store.async_remove()

    def _child(self, child_id: int) -> ChildMemory:
        return self._children.setdefault(str(child_id), {})

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self._children, SAVE_DELAY)

    def _prune(self, today: date) -> None:
        oldest = (today - KEPT).isoformat()
        for child in self._children.values():
            if "sick" in child:
                child["sick"] = [day for day in child["sick"] if day >= oldest]
            if "wrote" in child:
                child["wrote"] = [day for day in child["wrote"] if day >= oldest]
            if "done" in child:
                child["done"] = {task: due for task, due in child["done"].items() if due >= oldest}

    def note_sick(self, child_id: int, day: date) -> None:
        """Remember that the child was sick on `day`."""
        child = self._child(child_id)
        days = child.get("sick", [])
        if day.isoformat() not in days:
            child["sick"] = sorted([*days, day.isoformat()])
            self._prune(day)
            self._save()

    def forget_sick(self, child_id: int, day: date) -> None:
        """Forget that the child was sick on `day`, as when the report is taken back."""
        child = self._child(child_id)
        days = child.get("sick", [])
        if day.isoformat() in days:
            child["sick"] = [found for found in days if found != day.isoformat()]
            self._save()

    def was_sick(self, child_id: int, day: date) -> bool:
        """Whether the child is remembered as sick on `day`."""
        return day.isoformat() in self._child(child_id).get("sick", [])

    def sick_since(self, child_id: int, today: date, sick_today: bool) -> date | None:
        """The first school day of the child's sickness that goes on to today or the school day before.

        None where the child is sick neither today nor on the previous school day.
        """
        day = today if sick_today else previous_school_day(today)
        if day != today and not self.was_sick(child_id, day):
            return None
        first = day
        while self.was_sick(child_id, previous_school_day(first)):
            first = previous_school_day(first)
        return first

    def note_wrote(self, child_id: int, thread_id: int, at: datetime) -> None:
        """Remember that the family wrote in `thread_id` about the child, and that thread as the child's."""
        child = self._child(child_id)
        child["thread"] = {"id": thread_id, "at": at.isoformat()}
        days = child.get("wrote", [])
        day = dt_util.as_local(at).date()
        if day.isoformat() not in days:
            child["wrote"] = sorted([*days, day.isoformat()])
            self._prune(day)
        self._save()

    def wrote_on(self, child_id: int, day: date) -> bool:
        """Whether the family wrote about the child on `day` through Home Assistant."""
        return day.isoformat() in self._child(child_id).get("wrote", [])

    def thread(self, child_id: int) -> RememberedThread | None:
        """The thread remembered for the child; None where none is."""
        return self._child(child_id).get("thread")

    def ticked(self, child_id: int) -> set[str]:
        """The ids of the child's homework ticked at home."""
        return set(self._child(child_id).get("done", {}))

    def tick(self, child_id: int, task_id: str, due: date, done: bool) -> None:
        """Tick the child's homework `task_id`, due on `due`, or take the tick back."""
        child = self._child(child_id)
        ticked = dict(child.get("done", {}))
        if done:
            ticked[task_id] = due.isoformat()
        else:
            ticked.pop(task_id, None)
        child["done"] = ticked
        self._save()
