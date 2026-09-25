"""Pure decision logic of the climate control (no Home Assistant objects).

Times are Unix timestamps in seconds, temperatures in °C.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field

from .const import (
    CONF_COOL_MARGIN,
    CONF_FAST_TREND,
    CONF_HARD_MARGIN,
    CONF_LOCKOUT,
    CONF_MAX_WAIT,
    CONF_MIN_PAUSE,
    CONF_MIN_RUN,
    CONF_START_OFFSET,
    CONF_STOP_PAST_TARGET,
    CONF_STOP_POSITION,
    CONF_SUN_THRESHOLD,
    CONF_WARMTH_MARGIN,
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT,
    HVAC_MODE_HEAT_COOL,
    RUN_COOLING,
    RUN_HEATING,
    RUN_IDLE,
)


class RoomHistory:
    """Readings of the room sensor, treated as a step function.

    A reading holds until the next one, so a sensor that only reports changes
    works as well as one that reports periodically.
    """

    def __init__(self, keep_seconds: float) -> None:
        """Keep readings for at least `keep_seconds`."""
        self._keep = keep_seconds
        self._samples: deque[tuple[float, float]] = deque()
        self._latest: float | None = None  # newest reading, stored or not

    def add(self, ts: float, value: float) -> None:
        """Record a reading (ignores readings older than the newest one)."""
        if self._latest is not None and ts < self._latest:
            return
        self._latest = ts
        if self._samples and self._samples[-1][1] == value:
            return  # unchanged: the step function stays the same
        if self._samples and self._samples[-1][0] == ts:
            self._samples.pop()  # same moment: the newer reading wins
        self._samples.append((ts, value))
        # Drop readings that ended before the kept period; keep the one that
        # is still valid at its start.
        while len(self._samples) > 1 and self._samples[1][0] <= ts - self._keep:
            self._samples.popleft()

    def clear(self) -> None:
        """Forget all readings."""
        self._samples.clear()
        self._latest = None

    @property
    def empty(self) -> bool:
        """Return True without readings."""
        return not self._samples

    def value_at(self, ts: float) -> float | None:
        """Value valid at `ts` (the first reading if `ts` is before it)."""
        if not self._samples:
            return None
        value = self._samples[0][1]
        for sample_ts, sample_value in self._samples:
            if sample_ts > ts:
                break
            value = sample_value
        return value

    def mean(self, now: float, window: float) -> float | None:
        """Time-weighted mean over the last `window` seconds."""
        if not self._samples:
            return None
        start = max(now - window, self._samples[0][0])
        if start >= now:
            return self.value_at(now)
        total = 0.0
        points = [start] + [ts for ts, _ in self._samples if start < ts < now] + [now]
        for begin, end in zip(points, points[1:], strict=False):
            total += self.value_at(begin) * (end - begin)
        return total / (now - start)

    def change(self, now: float, window: float) -> float:
        """Current value minus the value `window` seconds ago (0 without data)."""
        current = self.value_at(now)
        before = self.value_at(now - window)
        if current is None or before is None:
            return 0.0
        return current - before

    def extreme_since(self, since: float, now: float, highest: bool) -> float | None:
        """Highest (or lowest) value between `since` and `now`."""
        start = self.value_at(since)
        if start is None:
            return None
        values = [start] + [v for ts, v in self._samples if since < ts <= now]
        return max(values) if highest else min(values)


@dataclass
class ForecastData:
    """Hourly forecast: temperature at the time stamp, radiation as the mean
    of the hour ending at the time stamp (Open-Meteo conventions)."""

    times: list[float]
    temperatures: list[float | None]
    radiation: list[float | None]


@dataclass
class ForecastWindow:
    """Forecast for [now, now + window]."""

    min_temp: float | None
    max_temp: float | None
    radiation: float | None


def forecast_window(
    data: ForecastData | None, now: float, minutes: float
) -> ForecastWindow | None:
    """Min/max outside temperature and mean radiation over the next minutes.

    Temperatures are interpolated linearly between the hourly values, so the
    extremes are at the window ends or at hourly values inside the window.
    """
    if data is None or not data.times or minutes <= 0:
        return None
    end = now + minutes * 60
    times = data.times
    if now < times[0] or end > times[-1]:
        return None

    def temp_at(ts: float) -> float | None:
        i = bisect_right(times, ts) - 1
        if i >= len(times) - 1:
            return data.temperatures[-1] if ts == times[-1] else None
        t0, t1 = data.temperatures[i], data.temperatures[i + 1]
        if t0 is None or t1 is None:
            return None
        return t0 + (t1 - t0) * (ts - times[i]) / (times[i + 1] - times[i])

    points = [now] + [ts for ts in times if now < ts < end] + [end]
    temps = [temp_at(ts) for ts in points]
    if any(t is None for t in temps):
        min_temp = max_temp = None
    else:
        min_temp, max_temp = min(temps), max(temps)

    # Radiation value k covers (times[k-1], times[k]].
    total = weight = 0.0
    for k in range(1, len(times)):
        begin, finish = max(times[k - 1], now), min(times[k], end)
        if finish <= begin:
            continue
        if data.radiation[k] is None:
            total = weight = 0.0
            break
        total += data.radiation[k] * (finish - begin)
        weight += finish - begin
    radiation = total / weight if weight else None
    return ForecastWindow(min_temp, max_temp, radiation)


@dataclass
class Situation:
    """Everything a decision is based on."""

    now: float
    run: str  # current run: idle / heating / cooling
    profile_mode: str
    period_low: float  # minimum of the active period
    period_high: float  # maximum of the active period
    room: float | None  # smoothed room temperature
    trend: float  # °C per 30 min
    forecast: ForecastWindow | None
    last_switch: float | None
    heat_ended: float | None
    cool_ended: float | None
    wait_since: float | None


@dataclass
class Decision:
    """Result of one evaluation."""

    run: str
    low: float | None  # minimum that applies in this mode (None: no heating)
    high: float | None  # maximum that applies in this mode (None: no cooling)
    stop_heat: float | None
    stop_cool: float | None
    heat_zone: bool = False
    cool_zone: bool = False
    hard: bool = False  # started because of the hard limit
    warmth_forecast: bool = False
    cool_forecast: bool = False
    warmth_coming: bool = False
    free_cooling: bool = False
    waiting: bool = False  # outside the range, but not running
    wait_since: float | None = None
    waited: float = 0.0  # minutes
    min_pause_until: float | None = None
    min_run_until: float | None = None
    lockout_until: float | None = None
    extra: dict = field(default_factory=dict)


def limits(mode: str, low: float, high: float) -> tuple[float | None, float | None]:
    """Minimum and maximum that apply in a profile mode."""
    if mode == HVAC_MODE_HEAT_COOL:
        return low, high
    if mode == HVAC_MODE_HEAT:
        return low, None
    if mode == HVAC_MODE_COOL:
        return None, high
    return None, None


def stop_points(
    mode: str, low: float | None, high: float | None, settings: dict
) -> tuple[float | None, float | None]:
    """Where a heating and a cooling run stop."""
    if mode == HVAC_MODE_HEAT_COOL and low is not None and high is not None:
        point = low + (high - low) * settings[CONF_STOP_POSITION] / 100
        return point, point
    past = settings[CONF_STOP_PAST_TARGET]
    return (
        low + past if low is not None else None,
        high - past if high is not None else None,
    )


def decide(s: Situation, settings: dict) -> Decision:
    """Decide whether to heat, cool or stay idle."""
    low, high = limits(s.profile_mode, s.period_low, s.period_high)
    stop_heat, stop_cool = stop_points(s.profile_mode, low, high, settings)
    d = Decision(RUN_IDLE, low, high, stop_heat, stop_cool)

    min_run = settings[CONF_MIN_RUN] * 60
    min_pause = settings[CONF_MIN_PAUSE] * 60
    lockout = settings[CONF_LOCKOUT] * 3600
    since_switch = s.now - s.last_switch if s.last_switch is not None else None

    room = s.room
    if room is None:
        d.run = RUN_IDLE
        return d

    if s.run == RUN_HEATING:
        can_stop = since_switch is None or since_switch >= min_run
        if stop_heat is None or (room >= stop_heat and can_stop):
            d.run = RUN_IDLE
        else:
            d.run = RUN_HEATING
            if room >= stop_heat and s.last_switch is not None:
                d.min_run_until = s.last_switch + min_run
        return d
    if s.run == RUN_COOLING:
        can_stop = since_switch is None or since_switch >= min_run
        if stop_cool is None or (room <= stop_cool and can_stop):
            d.run = RUN_IDLE
        else:
            d.run = RUN_COOLING
            if room <= stop_cool and s.last_switch is not None:
                d.min_run_until = s.last_switch + min_run
        return d

    # Idle: start a run?
    start = settings[CONF_START_OFFSET]
    hard_margin = settings[CONF_HARD_MARGIN]
    d.heat_zone = low is not None and room <= low - start
    d.cool_zone = high is not None and room >= high + start
    hard_cold = low is not None and room <= low - hard_margin
    hard_warm = high is not None and room >= high + hard_margin

    waited = (s.now - s.wait_since) / 60 if s.wait_since is not None else 0.0
    fc = s.forecast
    d.warmth_forecast = bool(
        fc is not None
        and low is not None
        and (
            (
                fc.min_temp is not None
                and fc.min_temp >= low + settings[CONF_WARMTH_MARGIN]
            )
            or (
                fc.radiation is not None
                and fc.radiation >= settings[CONF_SUN_THRESHOLD]
            )
        )
    )
    d.cool_forecast = bool(
        fc is not None
        and high is not None
        and fc.max_temp is not None
        and fc.max_temp <= high - settings[CONF_COOL_MARGIN]
    )
    fast = settings[CONF_FAST_TREND]
    max_wait = settings[CONF_MAX_WAIT]
    d.warmth_coming = d.warmth_forecast and s.trend > -fast and waited < max_wait
    d.free_cooling = d.cool_forecast and s.trend < fast and waited < max_wait

    since_cool = s.now - s.cool_ended if s.cool_ended is not None else None
    since_heat = s.now - s.heat_ended if s.heat_ended is not None else None
    heat_locked = since_cool is not None and since_cool < lockout
    cool_locked = since_heat is not None and since_heat < lockout
    can_start = since_switch is None or since_switch >= min_pause

    need_heat = low is not None and (
        hard_cold or (d.heat_zone and not d.warmth_coming and not heat_locked)
    )
    need_cool = high is not None and (
        hard_warm or (d.cool_zone and not d.free_cooling and not cool_locked)
    )
    if can_start and need_heat:
        d.run = RUN_HEATING
        d.hard = hard_cold
    elif can_start and need_cool:
        d.run = RUN_COOLING
        d.hard = hard_warm
    else:
        d.run = RUN_IDLE

    d.waiting = d.run == RUN_IDLE and (d.heat_zone or d.cool_zone)
    if d.waiting:
        d.wait_since = s.wait_since if s.wait_since is not None else s.now
        d.waited = (s.now - d.wait_since) / 60
        if not can_start and s.last_switch is not None:
            d.min_pause_until = s.last_switch + min_pause
        if d.heat_zone and heat_locked:
            d.lockout_until = s.cool_ended + lockout
        elif d.cool_zone and cool_locked:
            d.lockout_until = s.heat_ended + lockout
    else:
        d.wait_since = None
    # A run that starts now used up its waiting.
    d.extra["waited_before_start"] = waited
    return d


def round_setpoint(value: float, step: float, lowest: float, highest: float) -> float:
    """Round to the AC's step and clamp to its range."""
    if step <= 0:
        step = 0.5
    rounded = round(round(value / step) * step, 2)
    return min(max(rounded, lowest), highest)
