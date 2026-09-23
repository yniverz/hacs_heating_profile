"""Day/night start time settings."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingProfileConfigEntry
from .entity import HeatingProfileEntity

# The key doubles as the attribute name on HeatingProfileData.
DESCRIPTIONS = (
    TimeEntityDescription(
        key="day_start",
        name="Day starts",
        icon="mdi:weather-sunset-up",
    ),
    TimeEntityDescription(
        key="night_start",
        name="Night starts",
        icon="mdi:weather-sunset-down",
    ),
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

    @property
    def native_value(self) -> time:
        """Return the stored time."""
        return getattr(self._data, self.entity_description.key)

    async def async_set_value(self, value: time) -> None:
        """Store a new time."""
        self._data.async_update(**{self.entity_description.key: value})
