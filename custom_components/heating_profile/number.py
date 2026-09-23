"""Day/night temperature settings."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingProfileConfigEntry
from .const import MAX_TEMP, MIN_TEMP, TEMP_STEP
from .entity import HeatingProfileEntity

# The key doubles as the attribute name on HeatingProfileData.
DESCRIPTIONS = (
    NumberEntityDescription(
        key="day_temp",
        name="Day temperature",
        icon="mdi:weather-sunny",
    ),
    NumberEntityDescription(
        key="night_temp",
        name="Night temperature",
        icon="mdi:weather-night",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the temperature numbers."""
    async_add_entities(HeatingProfileNumber(entry, d) for d in DESCRIPTIONS)


class HeatingProfileNumber(HeatingProfileEntity, NumberEntity):
    """A stored temperature setting."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_step = TEMP_STEP

    @property
    def native_value(self) -> float:
        """Return the stored temperature."""
        return getattr(self._data, self.entity_description.key)

    async def async_set_native_value(self, value: float) -> None:
        """Store a new temperature."""
        self._data.async_update(**{self.entity_description.key: value})
