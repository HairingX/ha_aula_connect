"""The devices of a login's children, one for each institution Aula lists a child at."""

from __future__ import annotations

from collections.abc import Iterable

from aula_connect.api.models.module import AulaChildProfile
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


def child_identifier(entry_id: str, child: AulaChildProfile) -> tuple[str, str]:
    """The identifier of the child's device at its institution, under the entry `entry_id`.

    It is made of the entry's id, which stays as the entry is renamed, so a renamed login keeps
    its children's devices, and whether the user disabled them.
    """
    return (DOMAIN, f"{entry_id}_{child.id}")


def child_device_name(child: AulaChildProfile) -> str:
    """The child's first name and its institution's name."""
    return f"{child.first_name} · {child.institution_profile.institution_name}"


@callback
def disabled_children(hass: HomeAssistant, entry_id: str, children: Iterable[AulaChildProfile]) -> set[int]:
    """The ids of the children whose device the user has disabled."""
    registry = dr.async_get(hass)
    disabled = set[int]()
    for child in children:
        device = registry.async_get_child_device_by_identifier(child_identifier(entry_id, child), entry_id)
        if device is not None and device.disabled:
            disabled.add(child.id)
    return disabled


@callback
def async_tidy_child_devices(hass: HomeAssistant, entry_id: str, listed: list[AulaChildProfile]) -> None:
    """Remove each enabled child device whose child Aula no longer lists.

    A disabled device is kept: the user left it out, and removes it when Aula no longer lists it.
    """
    registry = dr.async_get(hass)
    listed_identifiers = {child_identifier(entry_id, child) for child in listed}
    for device in dr.async_child_entries_for_config_entry(registry, entry_id):
        if not device.disabled and not device.identifiers & listed_identifiers:
            registry.async_remove_device(device.id)


def listed_device(device: dr.AnyDeviceEntry, entry_id: str, listed: Iterable[AulaChildProfile]) -> bool:
    """Whether the device is the login's own, or a child's that Aula still lists."""
    if not isinstance(device, dr.ChildDeviceEntry):
        return True
    return any(child_identifier(entry_id, child) in device.identifiers for child in listed)
