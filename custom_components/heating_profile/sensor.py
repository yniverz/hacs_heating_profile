"""Target temperature sensor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from . import HeatingProfileConfigEntry
from .entity import HeatingProfileEntity

DESCRIPTION = SensorEntityDescription(
    key="target_temperature",
    name="Target temperature",
    icon="mdi:thermostat",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the target temperature sensor."""
    async_add_entities([HeatingProfileTargetSensor(entry, DESCRIPTION)])


class HeatingProfileTargetSensor(HeatingProfileEntity, SensorEntity):
    """The temperature that is active right now."""

    async def async_added_to_hass(self) -> None:
        """Also re-evaluate at the start of every minute."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._async_minute_tick, second=0)
        )

    @callback
    def _async_minute_tick(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        """Return the active temperature."""
        return self._data.temperature_at(dt_util.now().time())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose all settings so automations need only this sensor."""
        data = self._data
        return {
            "period": data.period_at(dt_util.now().time()),
            "day_temp": data.day_temp,
            "night_temp": data.night_temp,
            "day_start": data.day_start.isoformat(),
            "night_start": data.night_start.isoformat(),
        }
