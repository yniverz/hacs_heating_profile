"""Settings and schedule logic of one heating profile."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_DAY_START,
    DEFAULT_DAY_TEMP,
    DEFAULT_HVAC_MODE,
    DEFAULT_NIGHT_START,
    DEFAULT_NIGHT_TEMP,
    DOMAIN,
    HVAC_MODE_OFF,
    HVAC_MODES,
    PERIOD_DAY,
    PERIOD_NIGHT,
    SAVE_DELAY,
    STORAGE_VERSION,
)


def storage_key(entry_id: str) -> str:
    """Return the storage key of an entry."""
    return f"{DOMAIN}.{entry_id}"


def is_day(now: time, day_start: time, night_start: time) -> bool:
    """Return True if `now` falls in [day_start, night_start).

    Handles day ranges that cross midnight (day_start > night_start).
    An empty range (day_start == night_start) is always night.
    """
    if day_start <= night_start:
        return day_start <= now < night_start
    return now >= day_start or now < night_start


def _parse_time(value: Any, default: time) -> time:
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class HeatingProfileData:
    """Holds the settings of one heating profile and persists them."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize with defaults."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry_id)
        )
        self._listeners: list[CALLBACK_TYPE] = []
        self.day_temp: float = DEFAULT_DAY_TEMP
        self.night_temp: float = DEFAULT_NIGHT_TEMP
        self.day_start: time = DEFAULT_DAY_START
        self.night_start: time = DEFAULT_NIGHT_START
        self.hvac_mode: str = DEFAULT_HVAC_MODE
        # Mode restored by turn_on after the profile was switched off.
        self.last_active_mode: str = DEFAULT_HVAC_MODE
        # Manual day/night override, valid until the next scheduled switch.
        self.override_period: str | None = None
        self.override_until: datetime | None = None

    async def async_load(self) -> None:
        """Load stored values, falling back to defaults."""
        stored = await self._store.async_load() or {}
        self.day_temp = _parse_float(stored.get("day_temp"), DEFAULT_DAY_TEMP)
        self.night_temp = _parse_float(stored.get("night_temp"), DEFAULT_NIGHT_TEMP)
        self.day_start = _parse_time(stored.get("day_start"), DEFAULT_DAY_START)
        self.night_start = _parse_time(stored.get("night_start"), DEFAULT_NIGHT_START)
        if (mode := stored.get("hvac_mode")) in HVAC_MODES:
            self.hvac_mode = mode
        if (mode := stored.get("last_active_mode")) in HVAC_MODES and (
            mode != HVAC_MODE_OFF
        ):
            self.last_active_mode = mode
        until = stored.get("override_until")
        until = dt_util.parse_datetime(until) if isinstance(until, str) else None
        if stored.get("override_period") in (PERIOD_DAY, PERIOD_NIGHT) and until:
            self.override_period = stored["override_period"]
            self.override_until = until

    @callback
    def _as_dict(self) -> dict[str, Any]:
        return {
            "day_temp": self.day_temp,
            "night_temp": self.night_temp,
            "day_start": self.day_start.isoformat(),
            "night_start": self.night_start.isoformat(),
            "hvac_mode": self.hvac_mode,
            "last_active_mode": self.last_active_mode,
            "override_period": self.override_period,
            "override_until": (
                self.override_until.isoformat() if self.override_until else None
            ),
        }

    @callback
    def _async_changed(self) -> None:
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    @callback
    def async_update(self, **changes: Any) -> None:
        """Change settings, schedule a save and notify listeners."""
        for key, value in changes.items():
            if key.startswith("_") or not hasattr(self, key):
                raise AttributeError(key)
            setattr(self, key, value)
        if "day_start" in changes or "night_start" in changes:
            # The override was bound to the old schedule.
            self.override_period = self.override_until = None
        if self.hvac_mode != HVAC_MODE_OFF:
            self.last_active_mode = self.hvac_mode
        self._async_changed()

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

    def scheduled_period(self, now: datetime) -> str:
        """Return the period the schedule defines at `now`."""
        local = dt_util.as_local(now).time()
        if is_day(local, self.day_start, self.night_start):
            return PERIOD_DAY
        return PERIOD_NIGHT

    def override_active(self, now: datetime) -> bool:
        """Return True if a manual override applies at `now`."""
        return (
            self.override_period is not None
            and self.override_until is not None
            and now < self.override_until
        )

    def period(self, now: datetime) -> str:
        """Return the effective period at `now`, including overrides."""
        if self.override_active(now):
            assert self.override_period is not None
            return self.override_period
        return self.scheduled_period(now)

    def target_temperature(self, now: datetime) -> float:
        """Return the effective target temperature at `now`."""
        return self.day_temp if self.period(now) == PERIOD_DAY else self.night_temp

    def next_switch(self, now: datetime) -> datetime:
        """Return the next time the schedule switches between day and night."""
        local = dt_util.as_local(now)
        candidates = []
        for start in (self.day_start, self.night_start):
            candidate = datetime.combine(local.date(), start, local.tzinfo)
            if candidate <= local:
                candidate = datetime.combine(
                    local.date() + timedelta(days=1), start, local.tzinfo
                )
            candidates.append(candidate)
        return min(candidates)

    @callback
    def async_set_period(self, period: str, now: datetime) -> None:
        """Force a period until the next scheduled switch."""
        if period == self.scheduled_period(now):
            self.override_period = self.override_until = None
        else:
            self.override_period = period
            self.override_until = self.next_switch(now)
        self._async_changed()

    @callback
    def async_set_active_temperature(self, temperature: float, now: datetime) -> None:
        """Change the temperature of the period that is active at `now`."""
        key = "day_temp" if self.period(now) == PERIOD_DAY else "night_temp"
        self.async_update(**{key: temperature})

    @callback
    def async_expire_override(self, now: datetime) -> None:
        """Drop an override whose time has passed."""
        if self.override_period is not None and not self.override_active(now):
            self.override_period = self.override_until = None
            self._store.async_delay_save(self._as_dict, SAVE_DELAY)
