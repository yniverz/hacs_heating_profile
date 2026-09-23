"""The heating profile as a thermostat-like climate entity."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import HeatingProfileConfigEntry
from .const import (
    ATTR_DAY_START,
    ATTR_DAY_TEMPERATURE,
    ATTR_NIGHT_START,
    ATTR_NIGHT_TEMPERATURE,
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
    vol.Optional(ATTR_NIGHT_TEMPERATURE): _TEMPERATURE,
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
    """Shows the active target temperature; changes go to the stored profile."""

    # The entity takes the device name, e.g. climate.living_room.
    _attr_name = None
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO, HVACMode.OFF]
    _attr_preset_modes = [PERIOD_DAY, PERIOD_NIGHT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP

    async def async_added_to_hass(self) -> None:
        """Also re-evaluate at the start of every minute."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._async_minute_tick, second=0)
        )

    @callback
    def _async_minute_tick(self, _now: datetime) -> None:
        self._data.async_expire_override(dt_util.utcnow())
        self.async_write_ha_state()

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the stored mode."""
        return HVACMode(self._data.hvac_mode)

    @property
    def target_temperature(self) -> float:
        """Return the temperature of the active period."""
        return self._data.target_temperature(dt_util.utcnow())

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
            "night_temp": data.night_temp,
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
        """Change the temperature of the active period (and optionally mode)."""
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(mode)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._data.async_set_active_temperature(temperature, dt_util.utcnow())

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Store the mode."""
        self._data.async_update(hvac_mode=str(hvac_mode))

    async def async_turn_on(self) -> None:
        """Restore the last heat/cool/auto mode."""
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
        night_temperature: float | None = None,
        day_start: time | None = None,
        night_start: time | None = None,
    ) -> None:
        """Change any of the stored settings at once."""
        changes: dict[str, Any] = {
            "day_temp": day_temperature,
            "night_temp": night_temperature,
            "day_start": day_start,
            "night_start": night_start,
        }
        changes = {key: value for key, value in changes.items() if value is not None}
        if changes:
            self._data.async_update(**changes)
