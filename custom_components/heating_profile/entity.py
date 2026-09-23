"""Shared base entity for Heating Profile."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN

if TYPE_CHECKING:
    from . import HeatingProfileConfigEntry


class HeatingProfileEntity(Entity):
    """Base entity: one device per entry, state comes from the shared data."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry: HeatingProfileConfigEntry, description: EntityDescription
    ) -> None:
        """Initialize the entity."""
        self.entity_description = description
        self._data = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Custom",
            model="Heating profile",
        )

    async def async_added_to_hass(self) -> None:
        """Update state whenever any setting changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self._data.async_add_listener(self.async_write_ha_state))
