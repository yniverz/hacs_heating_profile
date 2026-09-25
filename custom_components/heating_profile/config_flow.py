"""Config and options flow for the Heating Profile integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ATTR_FAN_MODES, ATTR_HVAC_MODES
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_AC,
    CONF_ACTIVE_FAN_MODE,
    CONF_AUTO_TUNE,
    CONF_AVERAGE_WINDOW,
    CONF_AWAY_AFTER,
    CONF_COMMAND_GRACE,
    CONF_COMPRESSOR,
    CONF_COOL_MARGIN,
    CONF_FAST_TREND,
    CONF_HARD_MARGIN,
    CONF_IDLE_FAN_MODE,
    CONF_IDLE_HVAC_MODE,
    CONF_LOCKOUT,
    CONF_MAX_WAIT,
    CONF_MIN_PAUSE,
    CONF_MIN_RUN,
    CONF_OFFSET_MAX,
    CONF_OFFSET_STEP,
    CONF_OVERSHOOT,
    CONF_OVERSHOOT_WINDOW,
    CONF_PAUSE,
    CONF_PRESENCE,
    CONF_RAISE_IDLE,
    CONF_RAISE_MARGIN,
    CONF_ROOM_SENSOR,
    CONF_START_OFFSET,
    CONF_STOP_PAST_TARGET,
    CONF_STOP_POSITION,
    CONF_SUN_THRESHOLD,
    CONF_TREND_WINDOW,
    CONF_USE_FORECAST,
    CONF_WARMTH_MARGIN,
    CONTROL_DEFAULTS,
    DEFAULT_ACTIVE_FAN_MODE,
    DEFAULT_IDLE_FAN_MODE,
    DEFAULT_IDLE_HVAC_MODE,
    DEFAULT_NAME,
    DOMAIN,
)

SOURCE_KEYS = (CONF_ROOM_SENSOR, CONF_AC, CONF_COMPRESSOR, CONF_PRESENCE)

# key: (min, max, step, unit)
NUMBERS: dict[str, tuple[float, float, float, str]] = {
    CONF_START_OFFSET: (0.0, 3.0, 0.1, "°C"),
    CONF_HARD_MARGIN: (0.5, 5.0, 0.1, "°C"),
    CONF_STOP_POSITION: (0, 100, 5, "%"),
    CONF_STOP_PAST_TARGET: (0.0, 3.0, 0.1, "°C"),
    CONF_MIN_RUN: (0, 120, 1, "min"),
    CONF_MIN_PAUSE: (0, 120, 1, "min"),
    CONF_LOCKOUT: (0, 24, 0.5, "h"),
    CONF_MAX_WAIT: (0, 240, 5, "min"),
    CONF_FAST_TREND: (0.1, 3.0, 0.1, "°C/30 min"),
    CONF_WARMTH_MARGIN: (-5.0, 10.0, 0.5, "°C"),
    CONF_COOL_MARGIN: (-5.0, 10.0, 0.5, "°C"),
    CONF_SUN_THRESHOLD: (0, 1000, 10, "W/m²"),
    CONF_OFFSET_MAX: (0.5, 10.0, 0.5, "°C"),
    CONF_OFFSET_STEP: (0.1, 2.0, 0.1, "°C"),
    CONF_RAISE_IDLE: (5, 120, 1, "min"),
    CONF_RAISE_MARGIN: (0.0, 2.0, 0.1, "°C"),
    CONF_OVERSHOOT: (0.1, 5.0, 0.1, "°C"),
    CONF_OVERSHOOT_WINDOW: (5, 180, 5, "min"),
    CONF_PAUSE: (0, 1440, 5, "min"),
    CONF_COMMAND_GRACE: (10, 600, 5, "s"),
    CONF_AWAY_AFTER: (0, 1440, 5, "min"),
    CONF_AVERAGE_WINDOW: (1, 60, 1, "min"),
    CONF_TREND_WINDOW: (5, 180, 5, "min"),
}

SECTIONS: dict[str, tuple[str, ...]] = {
    "ac_modes": (CONF_IDLE_HVAC_MODE, CONF_IDLE_FAN_MODE, CONF_ACTIVE_FAN_MODE),
    "start_stop": (
        CONF_START_OFFSET,
        CONF_HARD_MARGIN,
        CONF_STOP_POSITION,
        CONF_STOP_PAST_TARGET,
        CONF_MIN_RUN,
        CONF_MIN_PAUSE,
        CONF_LOCKOUT,
    ),
    "waiting": (
        CONF_MAX_WAIT,
        CONF_FAST_TREND,
        CONF_WARMTH_MARGIN,
        CONF_COOL_MARGIN,
        CONF_SUN_THRESHOLD,
    ),
    "offsets": (
        CONF_AUTO_TUNE,
        CONF_OFFSET_MAX,
        CONF_OFFSET_STEP,
        CONF_RAISE_IDLE,
        CONF_RAISE_MARGIN,
        CONF_OVERSHOOT,
        CONF_OVERSHOOT_WINDOW,
    ),
    "manual": (CONF_PAUSE, CONF_COMMAND_GRACE),
    "presence": (CONF_AWAY_AFTER,),
    "measurement": (CONF_AVERAGE_WINDOW, CONF_TREND_WINDOW),
}


class HeatingProfileConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a heating profile. Multiple entries are allowed (one per room)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the profile name."""
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data={})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_NAME, default=DEFAULT_NAME): str}
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow (climate control)."""
        return HeatingProfileOptionsFlow()


def _entity(domains: list[str], device_class: str | None = None) -> selector.Selector:
    config: selector.EntitySelectorConfig = {"domain": domains}
    if device_class:
        config["device_class"] = device_class
    return selector.EntitySelector(config)


def _select(options: list[str]) -> selector.Selector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            mode=selector.SelectSelectorMode.DROPDOWN,
            custom_value=True,
        )
    )


def _number(key: str) -> selector.Selector:
    low, high, step, unit = NUMBERS[key]
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=low,
            max=high,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


class HeatingProfileOptionsFlow(OptionsFlow):
    """Step 1: sources (room sensor, AC, ...). Step 2: control settings."""

    def __init__(self) -> None:
        """Initialize."""
        self._sources: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the entities the control uses (or none: no control)."""
        current = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            sources = {k: user_input[k] for k in SOURCE_KEYS if user_input.get(k)}
            sources[CONF_USE_FORECAST] = user_input.get(
                CONF_USE_FORECAST, CONTROL_DEFAULTS[CONF_USE_FORECAST]
            )
            if bool(sources.get(CONF_ROOM_SENSOR)) != bool(sources.get(CONF_AC)):
                errors["base"] = "control_incomplete"
            elif not sources.get(CONF_AC):
                # No control: keep the other settings for later, drop sources.
                kept = {k: v for k, v in current.items() if k not in SOURCE_KEYS}
                return self.async_create_entry(data={**kept, **sources})
            else:
                self._sources = sources
                return await self.async_step_settings()
            current = {**current, **user_input}

        def suggested(key: str) -> dict[str, Any]:
            return {"suggested_value": current.get(key)}

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ROOM_SENSOR, description=suggested(CONF_ROOM_SENSOR)
                ): _entity(["sensor"], "temperature"),
                vol.Optional(CONF_AC, description=suggested(CONF_AC)): _entity(
                    ["climate"]
                ),
                vol.Optional(
                    CONF_COMPRESSOR, description=suggested(CONF_COMPRESSOR)
                ): _entity(["binary_sensor"]),
                vol.Optional(
                    CONF_PRESENCE, description=suggested(CONF_PRESENCE)
                ): _entity(
                    ["binary_sensor", "person", "device_tracker", "input_boolean"]
                ),
                vol.Optional(
                    CONF_USE_FORECAST,
                    default=current.get(
                        CONF_USE_FORECAST, CONTROL_DEFAULTS[CONF_USE_FORECAST]
                    ),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """All numbers and modes of the control, with defaults."""
        current = {
            CONF_IDLE_HVAC_MODE: DEFAULT_IDLE_HVAC_MODE,
            CONF_IDLE_FAN_MODE: DEFAULT_IDLE_FAN_MODE,
            CONF_ACTIVE_FAN_MODE: DEFAULT_ACTIVE_FAN_MODE,
            **CONTROL_DEFAULTS,
            **self.config_entry.options,
        }
        errors: dict[str, str] = {}
        if user_input is not None:
            flat: dict[str, Any] = {}
            for name in SECTIONS:
                flat.update(user_input.get(name, {}))
            if flat.get(CONF_HARD_MARGIN, 0) < flat.get(CONF_START_OFFSET, 0):
                errors["base"] = "hard_below_start"
            else:
                return self.async_create_entry(data={**flat, **self._sources})
            current = {**current, **flat}

        ac_state = self.hass.states.get(self._sources[CONF_AC])
        attrs = ac_state.attributes if ac_state else {}
        idle_modes = [
            m
            for m in (attrs.get(ATTR_HVAC_MODES) or [])
            if m not in ("off", "heat", "cool")
        ] or [DEFAULT_IDLE_HVAC_MODE]
        fan_modes = list(attrs.get(ATTR_FAN_MODES) or []) or [
            DEFAULT_IDLE_FAN_MODE,
            DEFAULT_ACTIVE_FAN_MODE,
        ]

        def field(key: str) -> tuple[vol.Marker, selector.Selector]:
            marker = vol.Required(key, default=current.get(key))
            if key == CONF_IDLE_HVAC_MODE:
                return marker, _select(idle_modes)
            if key in (CONF_IDLE_FAN_MODE, CONF_ACTIVE_FAN_MODE):
                return marker, _select(fan_modes)
            if key == CONF_AUTO_TUNE:
                return marker, selector.BooleanSelector()
            return marker, _number(key)

        schema = vol.Schema(
            {
                vol.Required(name): section(
                    vol.Schema(dict(field(k) for k in keys)),
                    {"collapsed": name != "ac_modes"},
                )
                for name, keys in SECTIONS.items()
            }
        )
        return self.async_show_form(
            step_id="settings", data_schema=schema, errors=errors
        )
