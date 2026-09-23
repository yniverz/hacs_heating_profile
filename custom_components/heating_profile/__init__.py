"""The Heating Profile integration.

A virtual device that controls nothing. It only stores user-editable
day/night temperatures and times that automations can read.
"""

from __future__ import annotations

from collections.abc import Callable
import datetime as dt
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store

# Note: `time` would be shadowed by the time.py platform module once it is
# imported into this package, so datetime.time is always referenced as dt.time.
from .const import (
    DEFAULT_DAY_START,
    DEFAULT_DAY_TEMP,
    DEFAULT_NIGHT_START,
    DEFAULT_NIGHT_TEMP,
    DOMAIN,
    PERIOD_DAY,
    PERIOD_NIGHT,
    SAVE_DELAY,
    STORAGE_VERSION,
)

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.TIME]

type HeatingProfileConfigEntry = ConfigEntry[HeatingProfileData]


def _storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"


def _parse_time(value: Any, default: dt.time) -> dt.time:
    try:
        return dt.time.fromisoformat(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_day(now: dt.time, day_start: dt.time, night_start: dt.time) -> bool:
    """Return True if `now` falls in [day_start, night_start).

    Handles day ranges that cross midnight (day_start > night_start).
    An empty range (day_start == night_start) is always night.
    """
    if day_start <= night_start:
        return day_start <= now < night_start
    return now >= day_start or now < night_start


class HeatingProfileData:
    """Holds the settings of one heating profile and persists them."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize with defaults."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, _storage_key(entry_id)
        )
        self._listeners: list[CALLBACK_TYPE] = []
        self.day_temp: float = DEFAULT_DAY_TEMP
        self.night_temp: float = DEFAULT_NIGHT_TEMP
        self.day_start: dt.time = DEFAULT_DAY_START
        self.night_start: dt.time = DEFAULT_NIGHT_START

    async def async_load(self) -> None:
        """Load stored values, falling back to defaults."""
        stored = await self._store.async_load() or {}
        self.day_temp = _parse_float(stored.get("day_temp"), DEFAULT_DAY_TEMP)
        self.night_temp = _parse_float(stored.get("night_temp"), DEFAULT_NIGHT_TEMP)
        self.day_start = _parse_time(stored.get("day_start"), DEFAULT_DAY_START)
        self.night_start = _parse_time(stored.get("night_start"), DEFAULT_NIGHT_START)

    @callback
    def _as_dict(self) -> dict[str, Any]:
        return {
            "day_temp": self.day_temp,
            "night_temp": self.night_temp,
            "day_start": self.day_start.isoformat(),
            "night_start": self.night_start.isoformat(),
        }

    @callback
    def async_update(self, **changes: Any) -> None:
        """Change one or more values, schedule a save and notify listeners."""
        for key, value in changes.items():
            if not hasattr(self, key) or key.startswith("_"):
                raise AttributeError(key)
            setattr(self, key, value)
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    async def async_flush(self) -> None:
        """Write the current values to disk immediately."""
        await self._store.async_save(self._as_dict())

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> Callable[[], None]:
        """Register a listener; returns a function that removes it."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    def period_at(self, now: dt.time) -> str:
        """Return the active period at the given local time."""
        if is_day(now, self.day_start, self.night_start):
            return PERIOD_DAY
        return PERIOD_NIGHT

    def temperature_at(self, now: dt.time) -> float:
        """Return the active temperature at the given local time."""
        return self.day_temp if self.period_at(now) == PERIOD_DAY else self.night_temp


async def async_setup_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> bool:
    """Set up a heating profile from a config entry."""
    data = HeatingProfileData(hass, entry.entry_id)
    await data.async_load()
    entry.runtime_data = data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> bool:
    """Unload a config entry."""
    # Flush pending delayed saves so a reload reads the latest values.
    await entry.runtime_data.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> None:
    """Delete the stored settings when the entry is removed."""
    await Store(hass, STORAGE_VERSION, _storage_key(entry.entry_id)).async_remove()
