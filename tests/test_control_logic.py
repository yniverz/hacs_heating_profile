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
    learn_step,
    round_setpoint,
)

S = dict(CONTROL_DEFAULTS)
H = 3600.0
M = 60.0
NOW = 100 * H


def test_history_step_function_mean_and_change() -> None:
    """Readings hold until the next one; mean is time-weighted."""
    h = RoomHistory(4 * H)
    assert h.mean(1000, 600) is None
    assert h.change(1000, 600) == 0.0
    h.add(0, 21.0)
    h.add(600, 20.0)  # 10 min at 21, then 20
    assert h.value_at(-5) == 21.0  # before the first reading
    assert h.value_at(599) == 21.0
    assert h.value_at(600) == 20.0
    assert h.mean(1200, 1200) == pytest.approx(20.5)
    assert h.mean(1200, 5000) == pytest.approx(20.5)  # only the known part
    assert h.mean(1200, 300) == pytest.approx(20.0)
    assert h.change(1200, 1200) == pytest.approx(-1.0)
    h.add(1200 + 32 * 60, 19.6)  # sparse sensor: long stable, then a step
    assert h.change(1200 + 32 * 60, 1800) == pytest.approx(-0.4)


def test_history_ignores_repeats_old_readings_and_prunes() -> None:
    """Repeats add nothing; out-of-order readings are dropped; old ones go."""
    h = RoomHistory(3600)
    h.add(0, 20.0)
    h.add(10, 20.0)
    h.add(5, 25.0)  # older than the newest reading -> ignored
    assert list(h._samples) == [(0, 20.0)]
    h.add(20, 21.0)
    h.add(20, 22.0)  # same moment: the newer reading wins
    assert list(h._samples) == [(0, 20.0), (20, 22.0)]
    for i in range(1, 50):
        h.add(i * 600, 20.0 + i % 2)
    assert h._samples[1][0] > 49 * 600 - 3600
    assert h.value_at(49 * 600 - 3600) is not None


def test_forecast_window_interpolates_and_weights() -> None:
    """Extremes at the window ends or at hourly values inside."""
    fc = ForecastData(
        times=[0.0, H, 2 * H, 3 * H],
        temperatures=[10.0, 20.0, 14.0, 12.0],
        radiation=[0.0, 100.0, 400.0, 0.0],  # mean of the hour ending there
    )
    w = forecast_window(fc, 0.5 * H, 60)
    assert w.min_temp == pytest.approx(15.0)
    assert w.max_temp == pytest.approx(20.0)
    assert w.radiation == pytest.approx(250.0)
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


def sit(**kw) -> Situation:
    base = dict(
        now=NOW,
        mode="neutral",
        profile_mode="heat_cool",
        period_low=21.0,
        period_high=25.0,
        room=23.0,
        trend=0.0,
        wait_forecast=None,
        exit_forecast=None,
        idle_since=None,
        mode_since=None,
        heat_ended=None,
        cool_ended=None,
        wait_since=None,
        drift_until=None,
        drift_side=None,
    )
    base.update(kw)
    return Situation(**base)


# Range 21-25: warmth needs outside > max + 1 = 26 the whole window, the early
# exit > max + 2 = 27; cool air < min - 1 = 20, the early exit < min - 2 = 19.
WARM = ForecastWindow(min_temp=26.5, max_temp=28.0, radiation=0.0)
HOT = ForecastWindow(min_temp=27.5, max_temp=30.0, radiation=0.0)
COOLAIR = ForecastWindow(min_temp=15.0, max_temp=19.5, radiation=0.0)
COLD = ForecastWindow(min_temp=10.0, max_temp=18.5, radiation=0.0)


def test_targets_and_start_points() -> None:
    """Targets inside the range; a mode starts near the limit."""
    d = decide(sit(), S)
    assert d.mode == "neutral"
    assert d.target_heat == pytest.approx(21.3) and d.target_cool == pytest.approx(24.7)
    assert decide(sit(room=21.2), S).mode == "neutral"
    d = decide(sit(room=21.1), S)
    assert d.mode == "heat" and d.reason == "no_forecast"
    assert decide(sit(room=24.8), S).mode == "neutral"
    assert decide(sit(room=24.9), S).mode == "cool"
    # Single-target profile modes only allow their side.
    assert decide(sit(room=30.0, profile_mode="heat"), S).mode == "neutral"
    assert decide(sit(room=15.0, profile_mode="cool"), S).mode == "neutral"
    assert decide(sit(room=15.0, profile_mode="off"), S).mode == "neutral"
    # No room reading: neutral.
    assert decide(sit(room=None, mode="heat"), S).mode == "neutral"


SUN = ForecastWindow(min_temp=10.0, max_temp=12.0, radiation=300.0)


def test_waiting_for_sun_is_time_limited() -> None:
    """Sun coming: wait, at most max_wait, not when falling fast."""
    d = decide(sit(room=21.0, wait_forecast=SUN), S)
    assert d.mode == "neutral" and d.waiting and d.warmth_coming
    assert not d.warm_hold and d.wait_since == NOW
    d = decide(sit(room=21.0, wait_forecast=SUN, trend=-0.3), S)
    assert d.mode == "heat" and d.reason == "fast"
    d = decide(sit(room=21.0, wait_forecast=SUN, wait_since=NOW - 60 * M), S)
    assert d.mode == "heat" and d.reason == "waited"
    # Back out of the zone: waiting ends.
    d = decide(sit(room=22.0, wait_forecast=SUN, wait_since=NOW - 600), S)
    assert not d.waiting and d.wait_since is None


def test_warm_outside_holds_until_the_hard_limit() -> None:
    """Warmer than max + 1 outside the whole window: heat only at the hard limit."""
    for room in (21.1, 21.0, 20.0, 19.6):
        d = decide(sit(room=room, wait_forecast=WARM), S)
        assert d.mode == "neutral" and d.held and d.warm_hold, room
        assert not d.waiting and d.wait_since is None
    # No time limit, no trend rule.
    d = decide(sit(room=20.0, wait_forecast=WARM, wait_since=NOW - 5 * H), S)
    assert d.mode == "neutral"
    d = decide(sit(room=20.0, wait_forecast=WARM, trend=-1.0), S)
    assert d.mode == "neutral"
    d = decide(sit(room=19.5, wait_forecast=WARM), S)
    assert d.mode == "heat" and d.reason == "hard"
    # In range: nothing held.
    d = decide(sit(room=22.0, wait_forecast=WARM), S)
    assert d.warm_hold and not d.held
    # Exactly max + 1 or only the range's minimum + 1: normal start.
    edge = ForecastWindow(min_temp=26.0, max_temp=27.0, radiation=0.0)
    assert decide(sit(room=21.0, wait_forecast=edge), S).mode == "heat"
    mild = ForecastWindow(min_temp=23.0, max_temp=24.0, radiation=0.0)
    assert decide(sit(room=21.0, wait_forecast=mild), S).mode == "heat"
    # Heat-only profile: measured against the period's maximum as well.
    d = decide(sit(room=21.0, wait_forecast=WARM, profile_mode="heat"), S)
    assert d.mode == "neutral" and d.held
    # Cooling mirrored: below min - 1 outside -> cool only at max + 1.5.
    d = decide(sit(room=26.0, wait_forecast=COOLAIR), S)
    assert d.mode == "neutral" and d.held and d.cool_hold
    d = decide(sit(room=26.5, wait_forecast=COOLAIR), S)
    assert d.mode == "cool" and d.reason == "hard"
    edge = ForecastWindow(min_temp=10.0, max_temp=20.0, radiation=0.0)
    assert decide(sit(room=24.9, wait_forecast=edge), S).mode == "cool"


def test_switch_gap_lockout_and_hard_limit() -> None:
    """Gap and lockout block normal starts, not the hard limit."""
    d = decide(sit(room=21.0, mode_since=NOW - 10 * M), S)
    assert d.mode == "neutral" and d.gap_until == NOW + 20 * M
    d = decide(sit(room=21.0, mode_since=NOW - H, cool_ended=NOW - H), S)
    assert d.mode == "neutral" and d.lockout_until == NOW + 5 * H
    d = decide(sit(room=19.5, mode_since=NOW - 60, cool_ended=NOW - 60), S)
    assert d.mode == "heat" and d.reason == "hard"
    # Heat mode: room far above the maximum -> cool right away.
    d = decide(sit(mode="heat", room=26.5, mode_since=NOW - 60), S)
    assert d.mode == "cool" and d.reason == "hard"
    d = decide(sit(mode="cool", room=19.5, mode_since=NOW - 60), S)
    assert d.mode == "heat" and d.reason == "hard"


def test_leaving_a_mode_when_the_ac_idles() -> None:
    """Heat -> neutral after the minimum time and 60 min of AC idling."""
    heat = dict(mode="heat", mode_since=NOW - 3 * H, room=21.5)
    assert decide(sit(**heat, idle_since=NOW - 60 * M), S).mode == "neutral"
    assert decide(sit(**heat, idle_since=NOW - 59 * M), S).mode == "heat"
    assert decide(sit(**heat, idle_since=None), S).mode == "heat"
    # Not before the minimum time in the mode.
    d = decide(sit(**{**heat, "mode_since": NOW - 90 * M}, idle_since=NOW - 80 * M), S)
    assert d.mode == "heat"
    # Not while the room is below the target.
    d = decide(sit(**{**heat, "room": 21.2}, idle_since=NOW - 80 * M), S)
    assert d.mode == "heat"
    cool = dict(mode="cool", mode_since=NOW - 3 * H, room=24.5)
    assert decide(sit(**cool, idle_since=NOW - H), S).mode == "neutral"
    # The profile no longer heats: neutral right away.
    assert decide(sit(mode="heat", profile_mode="cool", mode_since=NOW), S).mode == (
        "neutral"
    )


def test_early_exit_and_drift() -> None:
    """Really warm outside: heat -> neutral early; the room may drift."""
    heat = dict(mode="heat", mode_since=NOW - 40 * M, room=21.2)
    d = decide(sit(**heat, exit_forecast=HOT), S)
    assert d.mode == "neutral" and d.early_exit
    assert d.drift_side == "heat" and d.drift_until == NOW + 60 * M
    # Not warm enough, too early, below the minimum, or switched off.
    assert decide(sit(**heat, exit_forecast=WARM), S).mode == "heat"
    assert (
        decide(sit(**{**heat, "mode_since": NOW - 20 * M}, exit_forecast=HOT), S).mode
        == "heat"
    )
    assert decide(sit(**{**heat, "room": 20.9}, exit_forecast=HOT), S).mode == "heat"
    assert (
        decide(sit(**heat, exit_forecast=HOT), {**S, "early_exit": False}).mode
        == "heat"
    )
    sunny = ForecastWindow(min_temp=10.0, max_temp=12.0, radiation=450.0)
    assert decide(sit(**heat, exit_forecast=sunny), S).early_exit
    # Drifting: heating starts only at min - 0.3; cooling side unchanged.
    drift = dict(
        mode="neutral",
        mode_since=NOW - 40 * M,
        drift_until=NOW + 20 * M,
        drift_side="heat",
        wait_forecast=SUN,
    )
    d = decide(sit(**drift, room=20.8), S)
    assert (
        d.mode == "neutral"
        and not d.heat_zone
        and d.threshold_heat == pytest.approx(20.7)
    )
    assert d.threshold_cool == pytest.approx(24.9)
    d = decide(sit(**drift, room=20.7), S)
    assert d.mode == "heat"  # drift is the waiting: no extra forecast wait
    # Drift over: normal start point, reason says so.
    d = decide(sit(**{**drift, "drift_until": NOW - 1}, room=21.0), S)
    assert d.mode == "heat" and d.reason == "drift_over"
    # Cooling mirrored.
    cool = dict(mode="cool", mode_since=NOW - 40 * M, room=24.8)
    d = decide(sit(**cool, exit_forecast=COLD), S)
    assert d.mode == "neutral" and d.drift_side == "cool"
    assert decide(sit(**cool, exit_forecast=COOLAIR), S).mode == "cool"


def learn(**kw):
    base = dict(
        heating=True,
        target=21.3,
        room_now=20.9,
        room=20.9,
        room_long=20.9,
        offset=2.0,
        active=True,
        high_power=False,
        settings=S,
    )
    base.update(kw)
    return learn_step(**base)


def test_learning() -> None:
    """Raise when short, lower when overshooting while working."""
    assert learn() == pytest.approx(2.2)  # 0.5 * 0.4
    assert learn(active=False) == pytest.approx(2.2)  # AC thinks it's done
    assert learn(high_power=True) is None  # warm-up
    assert learn(room_long=21.2) is None  # within the dead band
    assert learn(room=21.2) is None  # the room is back near the target
    assert learn(room=21.9) is None  # warming up: the long average lags
    assert learn(room_now=21.9) is None  # just warmed: both averages lag
    assert (
        learn(room_now=20.0, room=20.0, room_long=20.0) is None
    )  # too far: not an offset
    assert learn(room_now=22.0, room=22.0, room_long=22.0) == pytest.approx(
        1.65
    )  # too warm
    assert (
        learn(room_now=22.0, room=22.0, room_long=22.0, active=False) is None
    )  # own cycling
    assert learn(room_long=None) is None
    assert learn(room=None) is None
    assert learn(active=None) is None  # activity unknown
    assert learn(
        room_now=20.3, room=20.3, room_long=20.3, settings={**S, "learn_max_error": 2}
    ) == pytest.approx(2.5)
    assert learn(offset=5.9) == pytest.approx(6.0)  # clamped to the maximum
    assert learn(offset=6.0) is None
    # Cooling: the AC gets target - offset; too warm -> raise.
    cool = dict(heating=False, target=24.7)
    assert learn(**cool, room_now=25.1, room=25.1, room_long=25.1) == pytest.approx(2.2)
    assert learn(**cool, room_now=24.2, room=24.2, room_long=24.2) == pytest.approx(
        1.75
    )
