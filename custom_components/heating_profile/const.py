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
#
# Layer 1 picks a mode (neutral / heat / cool) and changes it rarely.
# Layer 2 only sets the AC's setpoint (target + learned offset) and lets the
# AC modulate on its own.

CONTROL_STORAGE_VERSION = 1

# Sources
CONF_ROOM_SENSOR = "room_sensor"
CONF_AC = "ac_entity"
CONF_POWER_SENSOR = "power_sensor"
CONF_COMPRESSOR = "compressor_sensor"
CONF_FAN_SPEED = "fan_speed_sensor"
CONF_USE_FORECAST = "use_forecast"
SOURCE_KEYS = (
    CONF_ROOM_SENSOR,
    CONF_AC,
    CONF_POWER_SENSOR,
    CONF_COMPRESSOR,
    CONF_FAN_SPEED,
)

# AC modes
CONF_IDLE_HVAC_MODE = "idle_hvac_mode"
CONF_IDLE_FAN_MODE = "idle_fan_mode"
CONF_ACTIVE_FAN_MODE = "active_fan_mode"
CONF_STANDBY_FAN_MODE = "standby_fan_mode"

# Targets
CONF_TARGET_MARGIN = "target_margin"
CONF_START_MARGIN = "start_margin"
CONF_HARD_MARGIN = "hard_margin"
CONF_LOOK_AHEAD = "look_ahead"

# Mode changes
CONF_MIN_MODE_TIME = "min_mode_time"
CONF_IDLE_EXIT = "idle_exit"
CONF_SWITCH_GAP = "switch_gap"
CONF_LOCKOUT = "lockout"

# Waiting for free warmth / cooling before a mode starts
CONF_MAX_WAIT = "max_wait"
CONF_FAST_TREND = "fast_trend"
CONF_WARMTH_MARGIN = "warmth_margin"
CONF_COOL_MARGIN = "cool_margin"
CONF_SUN_THRESHOLD = "sun_threshold"

# Early switch to neutral when it gets really warm / cool outside
CONF_EARLY_EXIT = "early_exit"
CONF_EXIT_WARMTH_MARGIN = "exit_warmth_margin"
CONF_EXIT_COOL_MARGIN = "exit_cool_margin"
CONF_EXIT_WINDOW = "exit_window"
CONF_EXIT_SUN = "exit_sun"
CONF_DRIFT_MARGIN = "drift_margin"
CONF_DRIFT_TIME = "drift_time"

# Learning the setpoint offset
CONF_AUTO_TUNE = "auto_tune"
CONF_LEARN_INTERVAL = "learn_interval"
CONF_LEARN_GAIN = "learn_gain"
CONF_LEARN_DEADBAND = "learn_deadband"
CONF_LEARN_MAX_ERROR = "learn_max_error"
CONF_LEARN_SETTLE = "learn_settle"
CONF_LEARN_AVERAGE = "learn_average"
CONF_OFFSET_MIN = "offset_min"
CONF_OFFSET_MAX = "offset_max"
CONF_OFFSET_STEP = "offset_step"
CONF_ACTIVE_POWER = "active_power"
CONF_HIGH_POWER = "high_power"

# Anti short cycle (switch): the control runs the compressor in long runs
# and rests itself instead of letting the AC hold the setpoint.
CONF_CYCLE_STOP_MARGIN = "cycle_stop_margin"
CONF_CYCLE_MIN_RUN = "cycle_min_run"
CONF_CYCLE_MIN_REST = "cycle_min_rest"
CONF_CYCLE_RUN_MARGIN = "cycle_run_margin"
CONF_CYCLE_AVERAGE = "cycle_average"

# Manual changes on the AC
CONF_PAUSE = "pause"
CONF_COMMAND_GRACE = "command_grace"

# Measurement
CONF_AVERAGE_WINDOW = "average_window"
CONF_TREND_WINDOW = "trend_window"

# Defaults of the numeric/boolean settings (unit in the comment).
CONTROL_DEFAULTS: dict[str, float | bool] = {
    CONF_USE_FORECAST: True,
    CONF_TARGET_MARGIN: 0.3,  # °C: heat target = min + this, cool target = max - this
    CONF_START_MARGIN: 0.1,  # °C: heating starts at min + this (cooling max - this)
    CONF_HARD_MARGIN: 1.5,  # °C beyond min/max: switch mode right away
    CONF_LOOK_AHEAD: 30,  # min before a day/night switch: use the next range
    CONF_MIN_MODE_TIME: 120,  # min in heat/cool before the idle exit
    CONF_IDLE_EXIT: 60,  # min the AC idles in heat/cool -> neutral
    CONF_SWITCH_GAP: 30,  # min between neutral and heat/cool (both ways)
    CONF_LOCKOUT: 6,  # h between heating and cooling
    CONF_MAX_WAIT: 60,  # min to wait for free warmth/cooling before starting
    CONF_FAST_TREND: 0.3,  # °C per 30 min the wrong way: don't wait
    CONF_WARMTH_MARGIN: 1.0,  # °C: outside above the maximum + this = warmth
    CONF_COOL_MARGIN: 1.0,  # °C: outside below the minimum - this = cool air
    CONF_SUN_THRESHOLD: 250,  # W/m² mean global radiation
    CONF_EARLY_EXIT: True,
    CONF_EXIT_WARMTH_MARGIN: 2.0,  # °C above the maximum the whole window
    CONF_EXIT_COOL_MARGIN: 2.0,  # °C below the minimum the whole window
    CONF_EXIT_WINDOW: 120,  # min of forecast for the early exit
    CONF_EXIT_SUN: 400,  # W/m² mean global radiation for the early exit
    CONF_DRIFT_MARGIN: 0.3,  # °C the room may drift past the limit after it
    CONF_DRIFT_TIME: 60,  # min it may drift
    CONF_AUTO_TUNE: True,
    CONF_LEARN_INTERVAL: 20,  # min between learning steps
    CONF_LEARN_GAIN: 0.5,  # share of the error corrected per step
    CONF_LEARN_DEADBAND: 0.2,  # °C error that is ignored
    CONF_LEARN_MAX_ERROR: 1.0,  # °C: farther off is warm-up, not learned
    CONF_LEARN_SETTLE: 30,  # min after a mode/target change without learning
    CONF_LEARN_AVERAGE: 30,  # min room average used for learning
    CONF_OFFSET_MIN: -2.0,  # °C
    CONF_OFFSET_MAX: 6.0,  # °C
    CONF_OFFSET_STEP: 0.5,  # °C largest change per learning step
    CONF_ACTIVE_POWER: 50,  # W: the AC is working
    CONF_HIGH_POWER: 200,  # W: the AC works hard (warm-up, not learned)
    CONF_CYCLE_STOP_MARGIN: 0.8,  # °C: a run heats to min + this (cools max - this)
    CONF_CYCLE_MIN_RUN: 20,  # min
    CONF_CYCLE_MIN_REST: 15,  # min
    CONF_CYCLE_RUN_MARGIN: 5.0,  # °C: setpoint while running = room +/- this
    CONF_CYCLE_AVERAGE: 5,  # min room average for the run/rest decision
    CONF_PAUSE: 120,  # min after a manual change on the AC
    CONF_COMMAND_GRACE: 90,  # s after an own command
    CONF_AVERAGE_WINDOW: 10,  # min
    CONF_TREND_WINDOW: 30,  # min
}
DEFAULT_IDLE_HVAC_MODE = "fan_only"
DEFAULT_IDLE_FAN_MODE = "silent"
DEFAULT_ACTIVE_FAN_MODE = "auto"
DEFAULT_STANDBY_FAN_MODE = "silent"
DEFAULT_OFFSET = 2.0

# Control states (sensor "Control state")
STATE_DISABLED = "disabled"
STATE_UNAVAILABLE = "unavailable"
STATE_PAUSED = "paused"
STATE_OFF = "off"
STATE_NEUTRAL = "neutral"
STATE_WAITING = "waiting"
STATE_HEATING = "heating"
STATE_COOLING = "cooling"
CONTROL_STATES = [
    STATE_DISABLED,
    STATE_UNAVAILABLE,
    STATE_PAUSED,
    STATE_OFF,
    STATE_NEUTRAL,
    STATE_WAITING,
    STATE_HEATING,
    STATE_COOLING,
]

# Phases of the anti short cycle
PHASE_RUN = "run"
PHASE_REST = "rest"

# Modes (layer 1)
MODE_NEUTRAL = "neutral"
MODE_HEAT = "heat"
MODE_COOL = "cool"
MODES = (MODE_NEUTRAL, MODE_HEAT, MODE_COOL)

CONTROL_INTERVAL_SECONDS = 60
# The same command is repeated at most this often if the AC doesn't follow
# (once right away if the AC showed it and then undid it by itself).
RESEND_INTERVAL_SECONDS = 600
# Before the next part of a command, wait at most this long until the AC shows
# the previous one: some integrations (e.g. Midea) send the AC's whole last
# known state with every change, so a fan mode sent too early would carry the
# old mode and target temperature and undo them.
CONFIRM_TIMEOUT_SECONDS = 10.0
FORECAST_INTERVAL_MINUTES = 30
FORECAST_MAX_AGE_HOURS = 3
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
