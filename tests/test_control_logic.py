"""Unit tests of the pure control logic (no Home Assistant)."""

import pytest

from custom_components.heating_profile.const import CONTROL_DEFAULTS
from custom_components.heating_profile.control import (
    ForecastData,
    ForecastWindow,
    RoomHistory,
    Situation,
    decide,
    forecast_window,
    round_setpoint,
    stop_points,
)

S = dict(CONTROL_DEFAULTS)
H = 3600.0


def test_history_step_function_mean_change_extremes() -> None:
    """Readings hold until the next one; mean is time-weighted."""
    h = RoomHistory(4 * H)
    assert h.mean(1000, 600) is None
    assert h.change(1000, 600) == 0.0
    h.add(0, 21.0)
    h.add(600, 20.0)  # 10 min at 21, then 20
    assert h.value_at(-5) == 21.0  # before the first reading
    assert h.value_at(599) == 21.0
    assert h.value_at(600) == 20.0
    # Mean over the last 20 min at t=1200: 10 min 21, 10 min 20.
    assert h.mean(1200, 1200) == pytest.approx(20.5)
    # Window starts before the first reading: only the known part counts.
    assert h.mean(1200, 5000) == pytest.approx(20.5)
    assert h.mean(1200, 300) == pytest.approx(20.0)
    assert h.change(1200, 1200) == pytest.approx(-1.0)
    # Sparse sensor: long stable, then a step -> change sees the step.
    h.add(1200 + 32 * 60, 19.6)
    assert h.change(1200 + 32 * 60, 1800) == pytest.approx(-0.4)
    assert h.extreme_since(0, 5000, highest=True) == 21.0
    assert h.extreme_since(700, 5000, highest=False) == 19.6
    assert h.extreme_since(700, 5000, highest=True) == 20.0


def test_history_ignores_repeats_old_readings_and_prunes() -> None:
    """Repeated values add nothing; out-of-order readings are dropped."""
    h = RoomHistory(3600)
    h.add(0, 20.0)
    h.add(10, 20.0)
    h.add(5, 25.0)  # older than the newest -> ignored
    assert list(h._samples) == [(0, 20.0)]
    for i in range(1, 50):
        h.add(i * 600, 20.0 + i % 2)
    # Only readings still relevant for the last hour are kept (+1 before).
    assert h._samples[1][0] > 49 * 600 - 3600
    assert h.value_at(49 * 600 - 3600) is not None


def _forecast() -> ForecastData:
    times = [0.0, H, 2 * H, 3 * H]
    return ForecastData(
        times=times,
        temperatures=[10.0, 20.0, 14.0, 12.0],
        radiation=[0.0, 100.0, 400.0, 0.0],  # mean of the hour ending there
    )


def test_forecast_window_interpolates_and_weights() -> None:
    """Extremes at the window ends or at hourly values inside."""
    fc = _forecast()
    w = forecast_window(fc, 0.5 * H, 60)  # 00:30-01:30
    assert w.min_temp == pytest.approx(15.0)  # at 00:30
    assert w.max_temp == pytest.approx(20.0)  # at 01:00
    # Radiation: 30 min of hour 1 (100) + 30 min of hour 2 (400).
    assert w.radiation == pytest.approx(250.0)
    w = forecast_window(fc, 2 * H, 30)
    assert w.min_temp == pytest.approx(13.0) and w.max_temp == pytest.approx(14.0)
    assert w.radiation == pytest.approx(0.0)
    # Outside the data or no data: no forecast.
    assert forecast_window(fc, 2.9 * H, 60) is None
    assert forecast_window(fc, -1, 30) is None
    assert forecast_window(None, 0, 30) is None
    assert forecast_window(fc, 0, 0) is None
    fc.temperatures[1] = None
    w = forecast_window(fc, 0.5 * H, 60)
    assert w.min_temp is None and w.max_temp is None
    fc.radiation[2] = None
    assert forecast_window(fc, 0.5 * H, 60).radiation is None


def test_round_setpoint() -> None:
    """Rounded to the AC's step and clamped to its range."""
    assert round_setpoint(23.3, 0.5, 16, 30) == 23.5
    assert round_setpoint(23.2, 0.5, 16, 30) == 23.0
    assert round_setpoint(14.0, 0.5, 16, 30) == 16
    assert round_setpoint(33.0, 1, 16, 30) == 30
    assert round_setpoint(22.26, 0, 16, 30) == 22.5  # bad step -> 0.5


def test_stop_points() -> None:
    """Middle of the range in heat_cool, target +/- past in heat/cool."""
    assert stop_points("heat_cool", 21, 25, S) == (23, 23)
    assert stop_points("heat_cool", 21, 25, {**S, "stop_position": 0}) == (21, 21)
    assert stop_points("heat", 21, None, S) == pytest.approx((21.3, None))
    assert stop_points("cool", None, 25, S)[1] == pytest.approx(24.7)


def sit(**kw) -> Situation:
    base = dict(
        now=100 * H,
        run="idle",
        profile_mode="heat_cool",
        period_low=21.0,
        period_high=25.0,
        room=23.0,
        trend=0.0,
        forecast=None,
        last_switch=None,
        heat_ended=None,
        cool_ended=None,
        wait_since=None,
    )
    base.update(kw)
    return Situation(**base)


def test_decide_starts_and_stops() -> None:
    """Start 0.3 beyond the limit, stop at the stop point after min run."""
    assert decide(sit(room=20.8), S).run == "idle"
    d = decide(sit(room=20.7), S)
    assert d.run == "heating" and not d.hard
    assert decide(sit(room=25.3), S).run == "cooling"
    # Running: continues until the stop point...
    now = 100 * H
    running = dict(run="heating", last_switch=now - 30 * 60)
    assert decide(sit(room=22.9, **running), S).run == "heating"
    assert decide(sit(room=23.0, **running), S).run == "idle"
    # ...but not before the minimum run time.
    d = decide(sit(room=23.5, run="heating", last_switch=now - 5 * 60), S)
    assert d.run == "heating" and d.min_run_until == now - 5 * 60 + 20 * 60
    # Mode no longer heats -> stop right away.
    d = decide(
        sit(room=21.0, run="heating", last_switch=now - 60, profile_mode="cool"), S
    )
    assert d.run == "idle"
    # Room sensor missing -> idle.
    assert decide(sit(room=None, run="cooling"), S).run == "idle"


def test_decide_min_pause_lockout_hard_limit() -> None:
    """Minimum pause and lockout block normal starts, not the hard limit."""
    now = 100 * H
    d = decide(sit(room=20.5, last_switch=now - 10 * 60), S)
    assert d.run == "idle" and d.waiting and d.min_pause_until == now + 10 * 60
    d = decide(sit(room=20.5, cool_ended=now - 2 * H, last_switch=now - H), S)
    assert d.run == "idle" and d.lockout_until == now + 4 * H
    d = decide(sit(room=19.5, cool_ended=now - 2 * H, last_switch=now - H), S)
    assert d.run == "heating" and d.hard
    # The hard limit still respects the minimum pause.
    assert decide(sit(room=19.0, last_switch=now - 60), S).run == "idle"


def test_decide_waiting_for_warmth_and_cooling() -> None:
    """Wait while the forecast promises help, up to max_wait."""
    now = 100 * H
    warm = ForecastWindow(min_temp=22.0, max_temp=23.0, radiation=0.0)
    d = decide(sit(room=20.5, forecast=warm), S)
    assert d.run == "idle" and d.warmth_coming and d.wait_since == now
    # Outside only 21.5 at its lowest (< 21 + 1): heats.
    fc = ForecastWindow(min_temp=21.5, max_temp=30.0, radiation=0.0)
    assert decide(sit(room=20.5, forecast=fc), S).run == "heating"
    # Sun counts too.
    sun = ForecastWindow(min_temp=10.0, max_temp=12.0, radiation=300.0)
    assert decide(sit(room=20.5, forecast=sun), S).warmth_coming
    # Falling fast -> no waiting.
    d = decide(sit(room=20.5, forecast=warm, trend=-0.3), S)
    assert d.run == "heating" and d.warmth_forecast and not d.warmth_coming
    # Waited long enough.
    d = decide(sit(room=20.5, forecast=warm, wait_since=now - 60 * 60), S)
    assert d.run == "heating" and d.extra["waited_before_start"] == 60
    d = decide(sit(room=20.5, forecast=warm, wait_since=now - 59 * 60), S)
    assert d.run == "idle" and d.waited == pytest.approx(59)
    # Cooling: outside at most max - 2 the whole time.
    cool = ForecastWindow(min_temp=15.0, max_temp=23.0, radiation=0.0)
    assert decide(sit(room=25.4, forecast=cool), S).free_cooling
    hot = ForecastWindow(min_temp=15.0, max_temp=23.5, radiation=0.0)
    assert decide(sit(room=25.4, forecast=hot), S).run == "cooling"
    # Back in range -> waiting ends.
    d = decide(sit(room=21.0, forecast=warm, wait_since=now - 600), S)
    assert not d.waiting and d.wait_since is None


def test_decide_single_target_modes() -> None:
    """Heat mode never cools, cool mode never heats."""
    assert decide(sit(room=30.0, profile_mode="heat"), S).run == "idle"
    assert decide(sit(room=15.0, profile_mode="cool"), S).run == "idle"
    assert decide(sit(room=15.0, profile_mode="off"), S).run == "idle"
    d = decide(sit(room=20.6, profile_mode="heat"), S)
    assert d.run == "heating" and d.stop_heat == pytest.approx(21.3)
