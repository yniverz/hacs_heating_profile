"""Day/night start time settings."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingProfileConfigEntry
from .entity import HeatingProfileEntity

# The key doubles as the attribute name on HeatingProfileData.
DESCRIPTIONS = (
    TimeEntityDescription(key="day_start", translation_key="day_start"),
    TimeEntityDescription(key="night_start", translation_key="night_start"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the start times."""
    async_add_entities(HeatingProfileTime(entry, d) for d in DESCRIPTIONS)


class HeatingProfileTime(HeatingProfileEntity, TimeEntity):
    """A stored start time setting."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> time:
        """Return the stored time."""
        return getattr(self._data, self.entity_description.key)

    async def async_set_value(self, value: time) -> None:
        """Store a new time."""
        self._data.async_update(**{self.entity_description.key: value})
