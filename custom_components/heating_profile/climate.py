"""The heating profile as a thermostat-like climate entity."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter
import voluptuous as vol

from . import HeatingProfileConfigEntry
from .const import (
    ATTR_DAY_START,
    ATTR_DAY_TEMPERATURE,
    ATTR_DAY_TEMPERATURE_HIGH,
    ATTR_NIGHT_START,
    ATTR_NIGHT_TEMPERATURE,
    ATTR_NIGHT_TEMPERATURE_HIGH,
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT_COOL,
    HVAC_MODE_OFF,
    MAX_TEMP,
    MIN_TEMP,
    PERIOD_DAY,
    PERIOD_NIGHT,
    SERVICE_SET_PROFILE,
    TEMP_STEP,
)
from .entity import HeatingProfileEntity

DESCRIPTION = EntityDescription(key="climate", translation_key="profile")

_TEMPERATURE = vol.All(vol.Coerce(float), vol.Range(min=MIN_TEMP, max=MAX_TEMP))
SET_PROFILE_SCHEMA: dict[vol.Marker, Any] = {
    vol.Optional(ATTR_DAY_TEMPERATURE): _TEMPERATURE,
    vol.Optional(ATTR_DAY_TEMPERATURE_HIGH): _TEMPERATURE,
    vol.Optional(ATTR_NIGHT_TEMPERATURE): _TEMPERATURE,
    vol.Optional(ATTR_NIGHT_TEMPERATURE_HIGH): _TEMPERATURE,
    vol.Optional(ATTR_DAY_START): cv.time,
    vol.Optional(ATTR_NIGHT_START): cv.time,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the climate entity and the set_profile action."""
    async_add_entities([HeatingProfileClimate(entry, DESCRIPTION)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_PROFILE, SET_PROFILE_SCHEMA, "async_set_profile"
    )


class HeatingProfileClimate(HeatingProfileEntity, ClimateEntity):
    """Shows the active target or range; changes go to the stored profile.

    Heat shows the period's minimum as the target, cool its maximum and
    heat_cool the whole range (the Thermostat card then shows two handles).
    """

    # The entity takes the device name, e.g. climate.living_room.
    _attr_name = None
    _attr_hvac_modes = [
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.HEAT_COOL,
        HVACMode.OFF,
    ]
    _attr_preset_modes = [PERIOD_DAY, PERIOD_NIGHT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP

    def __init__(
        self, entry: HeatingProfileConfigEntry, description: EntityDescription
    ) -> None:
        """Initialize; the control's room sensor is the current temperature."""
        super().__init__(entry, description)
        controller = entry.runtime_data.controller
        self._room_sensor = controller.room_sensor if controller else None

    async def async_added_to_hass(self) -> None:
        """Also re-evaluate at the start of every minute."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._async_minute_tick, second=0)
        )
        if self._room_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._room_sensor], self._async_room_changed
                )
            )

    @callback
    def _async_room_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()

    @property
    def current_temperature(self) -> float | None:
        """The room sensor of the climate control, if one is set."""
        if not self._room_sensor:
            return None
        state = self.hass.states.get(self._room_sensor)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if unit in (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN):
            value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        return round(value, 2)

    @callback
    def _async_minute_tick(self, _now: datetime) -> None:
        self._data.async_expire_override(dt_util.utcnow())
        self.async_write_ha_state()

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the stored mode."""
        return HVACMode(self._data.hvac_mode)

    @property
    def target_temperature(self) -> float | None:
        """Return the target of the active period: minimum, or maximum when cooling."""
        mode = self._data.hvac_mode
        if mode in (HVAC_MODE_HEAT_COOL, HVAC_MODE_OFF):
            return None
        low, high = self._data.target_range(dt_util.utcnow())
        return high if mode == HVAC_MODE_COOL else low

    @property
    def target_temperature_low(self) -> float | None:
        """Return the minimum of the active period in heat_cool mode."""
        if self._data.hvac_mode != HVAC_MODE_HEAT_COOL:
            return None
        return self._data.target_range(dt_util.utcnow())[0]

    @property
    def target_temperature_high(self) -> float | None:
        """Return the maximum of the active period in heat_cool mode."""
        if self._data.hvac_mode != HVAC_MODE_HEAT_COOL:
            return None
        return self._data.target_range(dt_util.utcnow())[1]

    @property
    def preset_mode(self) -> str:
        """Return the active period (day/night)."""
        return self._data.period(dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the whole profile for automations and the card."""
        data = self._data
        now = dt_util.utcnow()
        return {
            "period": data.period(now),
            "day_temp": data.day_temp,
            "day_temp_high": data.day_temp_high,
            "night_temp": data.night_temp,
            "night_temp_high": data.night_temp_high,
            "day_start": data.day_start.isoformat(),
            "night_start": data.night_start.isoformat(),
            "override": data.override_active(now),
            "next_switch": dt_util.as_local(
                data.override_until
                if data.override_active(now) and data.override_until
                else data.next_switch(now)
            ).isoformat(),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Change the range of the active period (and optionally the mode).

        A single temperature sets the minimum (heat, off), the maximum (cool)
        or moves the whole range so it is centered on it (heat_cool).
        """
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(mode)
        now = dt_util.utcnow()
        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            mode = self._data.hvac_mode
            if mode == HVAC_MODE_COOL:
                high = temperature
            elif mode == HVAC_MODE_HEAT_COOL:
                cur_low, cur_high = self._data.target_range(now)
                width = cur_high - cur_low
                low = round((temperature - width / 2) / TEMP_STEP) * TEMP_STEP
                low = min(max(low, MIN_TEMP), MAX_TEMP - width)
                high = low + width
            else:
                low = temperature
        if low is not None or high is not None:
            self._data.async_set_active_range(now, low, high)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Store the mode."""
        self._data.async_update(hvac_mode=str(hvac_mode))

    async def async_turn_on(self) -> None:
        """Restore the last heat/cool/heat_cool mode."""
        self._data.async_update(hvac_mode=self._data.last_active_mode)

    async def async_turn_off(self) -> None:
        """Switch the profile off."""
        self._data.async_update(hvac_mode=HVAC_MODE_OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Force day or night until the next scheduled switch."""
        self._data.async_set_period(preset_mode, dt_util.utcnow())

    async def async_set_profile(
        self,
        day_temperature: float | None = None,
        day_temperature_high: float | None = None,
        night_temperature: float | None = None,
        night_temperature_high: float | None = None,
        day_start: time | None = None,
        night_start: time | None = None,
    ) -> None:
        """Change any of the stored settings at once."""
        changes: dict[str, Any] = {
            "day_temp": day_temperature,
            "day_temp_high": day_temperature_high,
            "night_temp": night_temperature,
            "night_temp_high": night_temperature_high,
            "day_start": day_start,
            "night_start": night_start,
        }
        changes = {key: value for key, value in changes.items() if value is not None}
        if changes:
            self._data.async_update(**changes)
