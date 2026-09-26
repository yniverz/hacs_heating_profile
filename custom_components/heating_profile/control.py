"""Pure decision logic of the climate control (no Home Assistant objects).

Times are Unix timestamps in seconds, temperatures in °C.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass

from .const import (
    CONF_COOL_MARGIN,
    CONF_DRIFT_MARGIN,
    CONF_DRIFT_TIME,
    CONF_EARLY_EXIT,
    CONF_EXIT_COOL_MARGIN,
    CONF_EXIT_SUN,
    CONF_EXIT_WARMTH_MARGIN,
    CONF_FAST_TREND,
    CONF_HARD_MARGIN,
    CONF_IDLE_EXIT,
    CONF_LEARN_DEADBAND,
    CONF_LEARN_GAIN,
    CONF_LEARN_MAX_ERROR,
    CONF_LOCKOUT,
    CONF_MAX_WAIT,
    CONF_MIN_MODE_TIME,
    CONF_OFFSET_MAX,
    CONF_OFFSET_MIN,
    CONF_OFFSET_STEP,
    CONF_START_MARGIN,
    CONF_SUN_THRESHOLD,
    CONF_SWITCH_GAP,
    CONF_TARGET_MARGIN,
    CONF_WARMTH_MARGIN,
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT,
    HVAC_MODE_HEAT_COOL,
    MODE_COOL,
    MODE_HEAT,
    MODE_NEUTRAL,
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
    """Everything a mode decision is based on."""

    now: float
    mode: str  # current mode: neutral / heat / cool
    profile_mode: str
    period_low: float  # minimum of the period that applies (look-ahead done)
    period_high: float  # maximum of that period
    room: float | None  # room temperature averaged over a few minutes
    trend: float  # °C per 30 min
    wait_forecast: ForecastWindow | None  # next max_wait minutes
    exit_forecast: ForecastWindow | None  # next exit_window minutes
    idle_since: float | None  # AC idle (not heating/cooling) since
    mode_since: float | None
    heat_ended: float | None
    cool_ended: float | None
    wait_since: float | None
    drift_until: float | None
    drift_side: str | None = None  # "heat" / "cool": which limit may drift


@dataclass
class Decision:
    """Result of one mode decision."""

    mode: str
    low: float | None  # minimum that applies (None: the profile doesn't heat)
    high: float | None  # maximum that applies (None: the profile doesn't cool)
    target_heat: float | None
    target_cool: float | None
    # Why a heat/cool mode starts now:
    # hard / no_forecast / fast / waited / drift_over / near
    reason: str = ""
    early_exit: bool = False  # left heat/cool because it gets warm/cool outside
    drift_until: float | None = None
    drift_side: str | None = None
    heat_zone: bool = False  # neutral and at/below the heating start point
    cool_zone: bool = False
    threshold_heat: float | None = None
    threshold_cool: float | None = None
    warmth_forecast: bool = False
    cool_forecast: bool = False
    warmth_coming: bool = False
    free_cooling: bool = False
    waiting: bool = False  # in a zone, but not started (yet)
    wait_since: float | None = None
    waited: float = 0.0  # minutes
    gap_until: float | None = None  # waiting for the switch gap
    lockout_until: float | None = None
    # Warm (cool) enough outside for the whole waiting window: no heat (cool)
    # mode until the hard limit, however long it takes.
    warm_hold: bool = False
    cool_hold: bool = False
    held: bool = False  # below/above the normal start point, held back


def limits(mode: str, low: float, high: float) -> tuple[float | None, float | None]:
    """Minimum and maximum that apply in a profile mode."""
    if mode == HVAC_MODE_HEAT_COOL:
        return low, high
    if mode == HVAC_MODE_HEAT:
        return low, None
    if mode == HVAC_MODE_COOL:
        return None, high
    return None, None


def _warm_outside(
    fc: ForecastWindow | None, high: float, margin: float, sun: float
) -> bool:
    """Warm enough to heat the room for free: the lowest outside temperature
    of the window is above the range's maximum + margin, or strong sun."""
    return bool(
        fc is not None
        and (
            (fc.min_temp is not None and fc.min_temp > high + margin)
            or (fc.radiation is not None and fc.radiation >= sun)
        )
    )


def _cool_outside(fc: ForecastWindow | None, low: float, margin: float) -> bool:
    """Cool enough to cool the room for free: the highest outside temperature
    of the window is below the range's minimum - margin."""
    return bool(
        fc is not None and fc.max_temp is not None and fc.max_temp < low - margin
    )


def decide(s: Situation, settings: dict) -> Decision:
    """Decide the mode (neutral / heat / cool)."""
    low, high = limits(s.profile_mode, s.period_low, s.period_high)
    margin = settings[CONF_TARGET_MARGIN]
    d = Decision(
        mode=s.mode,
        low=low,
        high=high,
        target_heat=low + margin if low is not None else None,
        target_cool=high - margin if high is not None else None,
    )
    if s.drift_until is not None and s.now < s.drift_until and s.drift_side:
        d.drift_until, d.drift_side = s.drift_until, s.drift_side
    room = s.room
    if room is None:
        d.mode = MODE_NEUTRAL
        return d

    hard_margin = settings[CONF_HARD_MARGIN]
    hard_cold = low is not None and room <= low - hard_margin
    hard_warm = high is not None and room >= high + hard_margin
    gap = settings[CONF_SWITCH_GAP] * 60
    since_mode = s.now - s.mode_since if s.mode_since is not None else None

    def elapsed(seconds: float) -> bool:
        return since_mode is None or since_mode >= seconds

    idle_long = (
        s.idle_since is not None
        and s.now - s.idle_since >= settings[CONF_IDLE_EXIT] * 60
    )

    if s.mode == MODE_HEAT:
        if low is None:  # the profile no longer heats
            d.mode = MODE_NEUTRAL
        elif hard_warm:
            d.mode, d.reason = MODE_COOL, "hard"
        elif (
            settings[CONF_EARLY_EXIT]
            and elapsed(gap)
            and room >= low
            and _warm_outside(
                s.exit_forecast,
                s.period_high,
                settings[CONF_EXIT_WARMTH_MARGIN],
                settings[CONF_EXIT_SUN],
            )
        ):
            d.mode, d.early_exit = MODE_NEUTRAL, True
            d.drift_until = s.now + settings[CONF_DRIFT_TIME] * 60
            d.drift_side = MODE_HEAT
        elif (
            elapsed(settings[CONF_MIN_MODE_TIME] * 60)
            and idle_long
            and d.target_heat is not None
            and room >= d.target_heat
        ):
            d.mode = MODE_NEUTRAL
        return d

    if s.mode == MODE_COOL:
        if high is None:
            d.mode = MODE_NEUTRAL
        elif hard_cold:
            d.mode, d.reason = MODE_HEAT, "hard"
        elif (
            settings[CONF_EARLY_EXIT]
            and elapsed(gap)
            and room <= high
            and _cool_outside(
                s.exit_forecast, s.period_low, settings[CONF_EXIT_COOL_MARGIN]
            )
        ):
            d.mode, d.early_exit = MODE_NEUTRAL, True
            d.drift_until = s.now + settings[CONF_DRIFT_TIME] * 60
            d.drift_side = MODE_COOL
        elif (
            elapsed(settings[CONF_MIN_MODE_TIME] * 60)
            and idle_long
            and d.target_cool is not None
            and room <= d.target_cool
        ):
            d.mode = MODE_NEUTRAL
        return d

    # Neutral: start a mode?
    drift_heat = d.drift_side == MODE_HEAT
    drift_cool = d.drift_side == MODE_COOL
    drift_ended_heat = s.drift_side == MODE_HEAT and not drift_heat
    drift_ended_cool = s.drift_side == MODE_COOL and not drift_cool
    start = settings[CONF_START_MARGIN]
    drift_margin = settings[CONF_DRIFT_MARGIN]
    if low is not None:
        d.threshold_heat = low - drift_margin if drift_heat else low + start
    if high is not None:
        d.threshold_cool = high + drift_margin if drift_cool else high - start
    d.heat_zone = d.threshold_heat is not None and room <= d.threshold_heat
    d.cool_zone = d.threshold_cool is not None and room >= d.threshold_cool
    fc = s.wait_forecast
    d.warm_hold = bool(
        low is not None
        and fc is not None
        and fc.min_temp is not None
        and fc.min_temp > s.period_high + settings[CONF_WARMTH_MARGIN]
    )
    d.cool_hold = bool(
        high is not None
        and fc is not None
        and fc.max_temp is not None
        and fc.max_temp < s.period_low - settings[CONF_COOL_MARGIN]
    )
    if d.warm_hold and d.heat_zone:
        d.heat_zone, d.held = False, True  # only the hard limit heats now
    if d.cool_hold and d.cool_zone:
        d.cool_zone, d.held = False, True

    waited = (s.now - s.wait_since) / 60 if s.wait_since is not None else 0.0
    fast = settings[CONF_FAST_TREND]
    max_wait = settings[CONF_MAX_WAIT]
    d.warmth_forecast = low is not None and _warm_outside(
        s.wait_forecast,
        s.period_high,
        settings[CONF_WARMTH_MARGIN],
        settings[CONF_SUN_THRESHOLD],
    )
    d.cool_forecast = high is not None and _cool_outside(
        s.wait_forecast, s.period_low, settings[CONF_COOL_MARGIN]
    )
    # While drifting after an early exit, the drift itself is the waiting;
    # when it ends, the mode comes back without waiting again.
    d.warmth_coming = (
        not (drift_heat or drift_ended_heat)
        and d.warmth_forecast
        and s.trend > -fast
        and waited < max_wait
    )
    d.free_cooling = (
        not (drift_cool or drift_ended_cool)
        and d.cool_forecast
        and s.trend < fast
        and waited < max_wait
    )

    lockout = settings[CONF_LOCKOUT] * 3600
    heat_locked = s.cool_ended is not None and s.now - s.cool_ended < lockout
    cool_locked = s.heat_ended is not None and s.now - s.heat_ended < lockout
    gap_ok = elapsed(gap)

    if hard_cold:
        d.mode, d.reason = MODE_HEAT, "hard"
    elif hard_warm:
        d.mode, d.reason = MODE_COOL, "hard"
    elif d.heat_zone and not d.warmth_coming and not heat_locked and gap_ok:
        d.mode = MODE_HEAT
        d.reason = _start_reason(
            d.warmth_forecast, s.trend <= -fast, waited, max_wait, drift_ended_heat
        )
    elif d.cool_zone and not d.free_cooling and not cool_locked and gap_ok:
        d.mode = MODE_COOL
        d.reason = _start_reason(
            d.cool_forecast, s.trend >= fast, waited, max_wait, drift_ended_cool
        )

    d.waiting = d.mode == MODE_NEUTRAL and (d.heat_zone or d.cool_zone)
    if d.waiting:
        d.wait_since = s.wait_since if s.wait_since is not None else s.now
        d.waited = (s.now - d.wait_since) / 60
        if not gap_ok and s.mode_since is not None:
            d.gap_until = s.mode_since + gap
        if d.heat_zone and heat_locked:
            d.lockout_until = s.cool_ended + lockout
        elif d.cool_zone and cool_locked:
            d.lockout_until = s.heat_ended + lockout
    return d


def _start_reason(
    forecast: bool, fast: bool, waited: float, max_wait: float, drift_ended: bool
) -> str:
    if drift_ended:
        return "drift_over"
    if not forecast:
        return "no_forecast"
    if fast:
        return "fast"
    if waited >= max_wait:
        return "waited"
    return "near"


def learn_step(
    *,
    heating: bool,
    target: float,
    room_now: float | None,
    room: float | None,
    room_long: float | None,
    offset: float,
    active: bool | None,
    high_power: bool | None,
    settings: dict,
) -> float | None:
    """New offset after one learning step, or None to leave it.

    Heating: the AC gets target + offset. Too cold while the AC idles or runs
    gently -> it stops too early -> raise. Too warm while it actively heats
    -> it heats too much -> lower. Too warm while idle is its own cycling or
    free warmth; too cold at high power is warm-up; both are not learned.
    Cooling is mirrored (the AC gets target - offset). The current reading
    (`room_now`) and the short average (`room`) must be off in the same
    direction as the learning average (`room_long`); otherwise the room is
    just changing and the averages lag behind.
    """
    if room_now is None or room is None or room_long is None or active is None:
        return None

    def err(value: float) -> float:
        return target - value if heating else value - target

    error = err(room_long)
    deadband = settings[CONF_LEARN_DEADBAND]
    errors = (error, err(room), err(room_now))
    if any(abs(e) < deadband for e in errors):
        return None
    if len({e > 0 for e in errors}) > 1:
        return None
    if abs(error) > settings[CONF_LEARN_MAX_ERROR]:
        return None
    if error > 0 and high_power:
        return None
    if error < 0 and not active:
        return None
    step = settings[CONF_OFFSET_STEP]
    delta = max(-step, min(step, settings[CONF_LEARN_GAIN] * error))
    new = min(max(offset + delta, settings[CONF_OFFSET_MIN]), settings[CONF_OFFSET_MAX])
    new = round(new, 2)
    return None if new == offset else new


def round_setpoint(value: float, step: float, lowest: float, highest: float) -> float:
    """Round to the AC's step and clamp to its range."""
    if step <= 0:
        step = 0.5
    rounded = round(round(value / step) * step, 2)
    return min(max(rounded, lowest), highest)
