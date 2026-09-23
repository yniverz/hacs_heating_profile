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

DEFAULT_DAY_TEMP = 21.0
DEFAULT_NIGHT_TEMP = 17.0
DEFAULT_DAY_START = time(6, 0)
DEFAULT_NIGHT_START = time(22, 0)

PERIOD_DAY = "day"
PERIOD_NIGHT = "night"
