"""Sensors that show what the climate control is doing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfIrradiance,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HeatingProfileConfigEntry
from .const import CONTROL_STATES
from .controller import ClimateController
from .entity import ControlEntity


@dataclass(frozen=True, kw_only=True)
class ControlSensorDescription(SensorEntityDescription):
    """Sensor with a function that reads the value from the controller."""

    value: Callable[[ClimateController], Any]
    attributes: Callable[[ClimateController], dict[str, Any] | None] = lambda _c: None


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _reason_since(c: ClimateController) -> dict[str, Any]:
    since = c.state.reason_since
    return {
        "since": None
        if since is None
        else dt_util.utc_from_timestamp(since).isoformat()
    }


def _fc(c: ClimateController, key: str) -> float | None:
    fc = c.view.forecast
    return None if fc is None else getattr(fc, key)


DESCRIPTIONS: tuple[ControlSensorDescription, ...] = (
    ControlSensorDescription(
        key="control_status",
        translation_key="control_status",
        value=lambda c: c.view.status,
        attributes=lambda c: c.view.attributes,
    ),
    ControlSensorDescription(
        key="control_reason",
        translation_key="control_reason",
        value=lambda c: c.state.reason,
        attributes=_reason_since,
    ),
    ControlSensorDescription(
        key="control_state",
        translation_key="control_state",
        device_class=SensorDeviceClass.ENUM,
        options=CONTROL_STATES,
        value=lambda c: c.view.state,
    ),
    ControlSensorDescription(
        key="room_average",
        translation_key="room_average",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: _round(c.view.room, 2),
    ),
    ControlSensorDescription(
        key="room_trend",
        translation_key="room_trend",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="°C/30 min",
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: _round(c.view.trend, 2),
    ),
    ControlSensorDescription(
        key="forecast_min",
        translation_key="forecast_min",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: _round(_fc(c, "min_temp")),
    ),
    ControlSensorDescription(
        key="forecast_max",
        translation_key="forecast_max",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: _round(_fc(c, "max_temp")),
    ),
    ControlSensorDescription(
        key="forecast_radiation",
        translation_key="forecast_radiation",
        device_class=SensorDeviceClass.IRRADIANCE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: _round(_fc(c, "radiation"), 0),
    ),
    ControlSensorDescription(
        key="ac_setpoint",
        translation_key="ac_setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.view.setpoint,
    ),
    ControlSensorDescription(
        key="waiting_until",
        translation_key="waiting_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.view.waiting_until,
    ),
    ControlSensorDescription(
        key="paused_until",
        translation_key="paused_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.view.paused_until,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors if the profile controls an AC."""
    if entry.runtime_data.controller is not None:
        async_add_entities(ControlSensor(entry, d) for d in DESCRIPTIONS)


class ControlSensor(ControlEntity, SensorEntity):
    """A value of the climate control."""

    entity_description: ControlSensorDescription

    @property
    def native_value(self) -> str | float | datetime | None:
        """Return the value."""
        return self.entity_description.value(self._controller)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        return self.entity_description.attributes(self._controller)
