"""Day/night minimum and maximum temperature settings."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingProfileConfigEntry
from .const import CONF_OFFSET_MAX, CONF_OFFSET_MIN, MAX_TEMP, MIN_TEMP, TEMP_STEP
from .entity import ControlEntity, HeatingProfileEntity

# The key doubles as the attribute name on HeatingProfileData. The minimum
# keeps its pre-0.4.0 key so existing entities keep their entity IDs.
DESCRIPTIONS = (
    NumberEntityDescription(key="day_temp", translation_key="day_temperature"),
    NumberEntityDescription(
        key="day_temp_high", translation_key="day_temperature_high"
    ),
    NumberEntityDescription(key="night_temp", translation_key="night_temperature"),
    NumberEntityDescription(
        key="night_temp_high", translation_key="night_temperature_high"
    ),
)


OFFSET_DESCRIPTIONS = (
    NumberEntityDescription(key="offset_heat", translation_key="offset_heat"),
    NumberEntityDescription(key="offset_cool", translation_key="offset_cool"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the temperature numbers (and the control's offsets)."""
    entities: list[NumberEntity] = [
        HeatingProfileNumber(entry, d) for d in DESCRIPTIONS
    ]
    if entry.runtime_data.controller is not None:
        entities += [OffsetNumber(entry, d) for d in OFFSET_DESCRIPTIONS]
    async_add_entities(entities)


class HeatingProfileNumber(HeatingProfileEntity, NumberEntity):
    """A stored temperature setting."""

    _attr_entity_category = EntityCategory.CONFIG
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


class OffsetNumber(ControlEntity, NumberEntity):
    """Learned offset between the target and the AC's setpoint.

    Heating: the AC gets target + offset, cooling: target - offset. Makes up
    for the AC's own sensor; learns by itself unless learning is off in the
    options, and can be set by hand.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    @property
    def native_min_value(self) -> float:
        """Lower limit from the options."""
        return float(self._controller.settings[CONF_OFFSET_MIN])

    @property
    def native_max_value(self) -> float:
        """Upper limit from the options."""
        return float(self._controller.settings[CONF_OFFSET_MAX])

    @property
    def native_value(self) -> float:
        """Return the current offset."""
        state = self._controller.state
        if self.entity_description.key == "offset_heat":
            return state.offset_heat
        return state.offset_cool

    async def async_set_native_value(self, value: float) -> None:
        """Set the offset by hand."""
        kind = "heat" if self.entity_description.key == "offset_heat" else "cool"
        await self._controller.async_set_offset(kind, value)
