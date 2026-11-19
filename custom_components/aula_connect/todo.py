"""Each child's homework from its weekly plan, as a to-do list ticked at home only."""

from __future__ import annotations

from datetime import date

from aula_connect.api.models.constants import AulaWeeklyPlanTaskType
from aula_connect.api.models.module import AulaChildProfile
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .aula_calendar_coordinator import WEEKLY_PLANS, AulaCalendarCoordinator
from .aula_data import AulaConfigEntry
from .const import DOMAIN
from .entity import child_unique_id, device_info
from .family import FamilyMemory

# The entities read what the coordinators fetched, so updating them never waits on Aula.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: AulaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add each child's homework, where the institution has weekly plans."""
    runtime = entry.runtime_data
    if not runtime.data_coordinator.weekly_plans_supported():
        return
    async_add_entities(
        AulaHomeworkList(runtime.calendar_coordinator, runtime.memory, child) for child in runtime.data_coordinator.data.children
    )


SUMMARY_LENGTH = 75


def _summary(subject: str, text: str) -> str:
    """The first line of a task's text, shortened to SUMMARY_LENGTH, after its subject."""
    lines = text.strip().splitlines() or [""]
    line = lines[0] if len(lines[0]) <= SUMMARY_LENGTH else lines[0][: SUMMARY_LENGTH - 3] + "..."
    return f"[{subject}] {line}" if subject else line


class AulaHomeworkList(TodoListEntity):
    """The tasks of a child's weekly plan, each due on its day, keyed by the task's id.

    Ticking one is remembered in Home Assistant only; Aula is never told. A task is neither added
    nor removed here, as the weekly plan is the teachers'.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "homework"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, coordinator: AulaCalendarCoordinator, memory: FamilyMemory, child: AulaChildProfile) -> None:
        self.coordinator = coordinator
        self._memory = memory
        self._child = child
        self._attr_unique_id = child_unique_id(coordinator, "homework", child)
        self._attr_device_info = device_info(coordinator)
        self._attr_translation_placeholders = {"name": child.first_name, "institution": child.institution_profile.institution_name}
        self._attr_todo_items = []
        self._due: dict[str, date] = {}
        """The day each task is due, by its uid."""

    async def async_added_to_hass(self) -> None:
        """Have the coordinator read the child's weekly plans, and follow its reads."""
        await super().async_added_to_hass()
        await self.coordinator.async_add_weekly_plan_key(self._child)
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))
        self._update()

    async def async_will_remove_from_hass(self) -> None:
        """Stop having the coordinator read the child's weekly plans for this list."""
        await self.coordinator.async_remove_weekly_plan_key(self._child)
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update()
        self.async_write_ha_state()

    def _update(self) -> None:
        data = self.coordinator.data
        self._attr_available = self.coordinator.last_update_success and WEEKLY_PLANS not in data.unreadable
        ticked = self._memory.ticked(self._child.id)
        items = list[TodoItem]()
        due_days = list[tuple[str, date]]()
        for plan in self.coordinator.get_weekly_plans(self._child):
            for daily in plan.daily_plans:
                for task in daily.tasks:
                    if task.type != AulaWeeklyPlanTaskType.TASK:
                        continue
                    uid = str(task.id)
                    due_days.append((uid, daily.date))
                    items.append(
                        TodoItem(
                            summary=_summary(task.pill, task.title or task.content),
                            uid=uid,
                            status=TodoItemStatus.COMPLETED if uid in ticked else TodoItemStatus.NEEDS_ACTION,
                            due=daily.date,
                            description=task.content or None,
                        )
                    )
        self._due = dict(due_days)
        self._attr_todo_items = sorted({item.uid: item for item in items}.values(), key=lambda item: (str(item.due), str(item.uid)))

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Tick or untick a task at home; nothing else of it can change.

        Raises:
            ServiceValidationError: The task is not on the list, or more than its tick would change.
        """
        found = next((known for known in self._attr_todo_items or [] if known.uid == item.uid), None)
        if found is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="homework_not_found")
        if (item.summary, item.due, item.description) != (found.summary, found.due, found.description):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="homework_only_ticked")
        uid = found.uid or ""
        self._memory.tick(self._child.id, uid, self._due[uid], item.status == TodoItemStatus.COMPLETED)
        self._update()
        self.async_write_ha_state()
