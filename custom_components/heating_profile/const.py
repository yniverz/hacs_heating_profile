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
