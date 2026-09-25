"""Constants for the Heating Profile integration."""

from __future__ import annotations

from datetime import time

DOMAIN = "heating_profile"

DEFAULT_NAME = "Heating profile"

STORAGE_VERSION = 1
SAVE_DELAY = 5

MIN_TEMP = 5.0
MAX_TEMP = 30.0
TEMP_STEP = 0.5

# Minimum/maximum of the comfort range per period. The minimum is what
# heating aims for, the maximum what cooling aims for.
DEFAULT_DAY_TEMP = 21.0
DEFAULT_NIGHT_TEMP = 17.0
DEFAULT_DAY_TEMP_HIGH = 25.0
DEFAULT_NIGHT_TEMP_HIGH = 24.0
# Smallest allowed gap between minimum and maximum.
MIN_RANGE = 1.0
DEFAULT_DAY_START = time(6, 0)
DEFAULT_NIGHT_START = time(22, 0)

PERIOD_DAY = "day"
PERIOD_NIGHT = "night"

# Values of homeassistant.components.climate.HVACMode, kept as plain strings
# so the storage layer does not depend on the climate component.
HVAC_MODE_HEAT = "heat"
HVAC_MODE_COOL = "cool"
HVAC_MODE_HEAT_COOL = "heat_cool"
HVAC_MODE_OFF = "off"
HVAC_MODES = (HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_HEAT_COOL, HVAC_MODE_OFF)
# Versions up to 0.3.0 stored the range mode as "auto".
LEGACY_HVAC_MODES = {"auto": HVAC_MODE_HEAT_COOL}
DEFAULT_HVAC_MODE = HVAC_MODE_HEAT

SERVICE_SET_PROFILE = "set_profile"
ATTR_DAY_TEMPERATURE = "day_temperature"
ATTR_NIGHT_TEMPERATURE = "night_temperature"
ATTR_DAY_TEMPERATURE_HIGH = "day_temperature_high"
ATTR_NIGHT_TEMPERATURE_HIGH = "night_temperature_high"
ATTR_DAY_START = "day_start"
ATTR_NIGHT_START = "night_start"

CARD_FILENAME = "heating-profile-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"

# --- Climate control (optional per profile, set up in the options flow) ---

CONTROL_STORAGE_VERSION = 1

# Sources
CONF_ROOM_SENSOR = "room_sensor"
CONF_AC = "ac_entity"
CONF_COMPRESSOR = "compressor_sensor"
CONF_PRESENCE = "presence_entity"
CONF_USE_FORECAST = "use_forecast"

# AC modes
CONF_IDLE_HVAC_MODE = "idle_hvac_mode"
CONF_IDLE_FAN_MODE = "idle_fan_mode"
CONF_ACTIVE_FAN_MODE = "active_fan_mode"

# Start / stop
CONF_START_OFFSET = "start_offset"
CONF_HARD_MARGIN = "hard_margin"
CONF_STOP_POSITION = "stop_position"
CONF_STOP_PAST_TARGET = "stop_past_target"
CONF_MIN_RUN = "min_run"
CONF_MIN_PAUSE = "min_pause"
CONF_LOCKOUT = "lockout"

# Waiting for free warmth / cooling
CONF_MAX_WAIT = "max_wait"
CONF_FAST_TREND = "fast_trend"
CONF_WARMTH_MARGIN = "warmth_margin"
CONF_COOL_MARGIN = "cool_margin"
CONF_SUN_THRESHOLD = "sun_threshold"

# Setpoint offsets
CONF_AUTO_TUNE = "auto_tune"
CONF_OFFSET_MAX = "offset_max"
CONF_OFFSET_STEP = "offset_step"
CONF_RAISE_IDLE = "raise_idle"
CONF_RAISE_MARGIN = "raise_margin"
CONF_OVERSHOOT = "overshoot"
CONF_OVERSHOOT_WINDOW = "overshoot_window"

# Manual changes on the AC
CONF_PAUSE = "pause"
CONF_COMMAND_GRACE = "command_grace"

# Presence
CONF_AWAY_AFTER = "away_after"

# Measurement
CONF_AVERAGE_WINDOW = "average_window"
CONF_TREND_WINDOW = "trend_window"

# Defaults of the numeric/boolean settings (unit in the comment).
CONTROL_DEFAULTS: dict[str, float | bool] = {
    CONF_START_OFFSET: 0.3,  # °C beyond minimum/maximum before a run starts
    CONF_HARD_MARGIN: 1.5,  # °C beyond minimum/maximum: start regardless
    CONF_STOP_POSITION: 50,  # % into the range where a heat_cool run stops
    CONF_STOP_PAST_TARGET: 0.3,  # °C past the target in heat/cool mode
    CONF_MIN_RUN: 20,  # min
    CONF_MIN_PAUSE: 20,  # min
    CONF_LOCKOUT: 6,  # h between heating and cooling
    CONF_MAX_WAIT: 60,  # min to wait for free warmth/cooling
    CONF_FAST_TREND: 0.3,  # °C per 30 min the wrong way: don't wait
    CONF_WARMTH_MARGIN: 1.0,  # °C above the minimum outside counts as warmth
    CONF_COOL_MARGIN: 2.0,  # °C below the maximum outside counts as cool air
    CONF_SUN_THRESHOLD: 250,  # W/m² mean global radiation
    CONF_AUTO_TUNE: True,
    CONF_OFFSET_MAX: 5.0,  # °C
    CONF_OFFSET_STEP: 0.5,  # °C
    CONF_RAISE_IDLE: 15,  # min compressor idle during a run -> raise offset
    CONF_RAISE_MARGIN: 0.2,  # °C short of the stop point
    CONF_OVERSHOOT: 1.0,  # °C past the stop point after a run -> lower offset
    CONF_OVERSHOOT_WINDOW: 30,  # min after a run
    CONF_PAUSE: 120,  # min after a manual change on the AC
    CONF_COMMAND_GRACE: 90,  # s after an own command
    CONF_AWAY_AFTER: 60,  # min absent -> AC off while idle
    CONF_AVERAGE_WINDOW: 10,  # min
    CONF_TREND_WINDOW: 30,  # min
    CONF_USE_FORECAST: True,
}
DEFAULT_IDLE_HVAC_MODE = "fan_only"
DEFAULT_IDLE_FAN_MODE = "silent"
DEFAULT_ACTIVE_FAN_MODE = "auto"
DEFAULT_OFFSET = 2.0

# Control states (sensor "Control state")
STATE_DISABLED = "disabled"
STATE_UNAVAILABLE = "unavailable"
STATE_PAUSED = "paused"
STATE_OFF = "off"
STATE_AWAY = "away"
STATE_IDLE = "idle"
STATE_WAITING = "waiting"
STATE_HEATING = "heating"
STATE_COOLING = "cooling"
CONTROL_STATES = [
    STATE_DISABLED,
    STATE_UNAVAILABLE,
    STATE_PAUSED,
    STATE_OFF,
    STATE_AWAY,
    STATE_IDLE,
    STATE_WAITING,
    STATE_HEATING,
    STATE_COOLING,
]

# Runs (what the AC is asked to do)
RUN_IDLE = "idle"
RUN_HEATING = "heating"
RUN_COOLING = "cooling"

CONTROL_INTERVAL_SECONDS = 60
# The same command is repeated at most this often if the AC doesn't follow.
RESEND_INTERVAL_SECONDS = 600
FORECAST_INTERVAL_MINUTES = 30
FORECAST_MAX_AGE_HOURS = 3
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
