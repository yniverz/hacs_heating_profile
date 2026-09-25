"""Climate control scenarios against a real Home Assistant core.

The AC is simulated: its state is set by the tests, its services are mocked
and recorded.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.heating_profile.const import DOMAIN
from custom_components.heating_profile.control import ForecastWindow

ROOM = "sensor.room"
AC = "climate.ac"
COMP = "binary_sensor.compressor"
PRES = "binary_sensor.presence"
PROFILE = "climate.living_room"
STATUS = "sensor.living_room_control_status"
REASON = "sensor.living_room_control_reason"
STATE = "sensor.living_room_control_state"
SWITCH = "switch.living_room_climate_control"
BUTTON = "button.living_room_end_pause"
OFFSET_HEAT = "number.living_room_heating_offset"
OFFSET_COOL = "number.living_room_cooling_offset"
SERVICES = ("set_hvac_mode", "set_temperature", "set_fan_mode")

OPTIONS = {
    "room_sensor": ROOM,
    "ac_entity": AC,
    "compressor_sensor": COMP,
    "presence_entity": PRES,
    "use_forecast": False,
}


def set_ac(
    hass: HomeAssistant, mode: str, temp: float = 20.0, fan: str = "silent"
) -> None:
    hass.states.async_set(
        AC,
        mode,
        {
            "hvac_modes": ["off", "auto", "cool", "dry", "heat", "fan_only"],
            "fan_modes": ["silent", "low", "medium", "high", "full", "auto"],
            "min_temp": 16,
            "max_temp": 30,
            "target_temp_step": 0.5,
            "temperature": temp,
            "fan_mode": fan,
            "current_temperature": 21,
        },
    )


class StubForecast:
    """Stands in for the Open-Meteo source."""

    def __init__(self) -> None:
        self.value: ForecastWindow | None = None

    def window(self, now: float, minutes: float) -> ForecastWindow | None:
        return self.value


def controller(entry: MockConfigEntry):
    return entry.runtime_data.controller


def sent(calls: dict[str, list]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for name, lst in calls.items():
        out += [
            (name, {k: v for k, v in c.data.items() if k != ATTR_ENTITY_ID})
            for c in lst
        ]
        lst.clear()
    return out


def st(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


def hm(ts: float) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%H:%M")


async def advance(hass: HomeAssistant, freezer, minutes: int) -> None:
    """Let `minutes` pass, one control tick per minute."""
    for _ in range(minutes):
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def setup_control(
    hass: HomeAssistant,
    local_time,
    freezer,
    options: dict[str, Any] | None = None,
    hour: int = 12,
) -> tuple[MockConfigEntry, dict[str, list]]:
    local_time(hour)
    hass.states.async_set(ROOM, "23.0", {"unit_of_measurement": "°C"})
    # Compressor "on" by default, so the offset tuning stays out of the way.
    hass.states.async_set(COMP, "on")
    hass.states.async_set(PRES, "on")
    set_ac(hass, "fan_only")
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living room", data={}, options=options or OPTIONS
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Mock the climate services only now (the climate component registered
    # the real ones during setup); the profile is changed through its data.
    calls = {s: async_mock_service(hass, CLIMATE_DOMAIN, s) for s in SERVICES}
    await set_profile_mode(hass, entry, "heat_cool")
    # Some room history, so the 10-min average is a real average.
    await advance(hass, freezer, 12)
    return entry, calls


async def set_profile_mode(
    hass: HomeAssistant, entry: MockConfigEntry, mode: str
) -> None:
    entry.runtime_data.profile.async_update(hvac_mode=mode)
    await hass.async_block_till_done()


@pytest.fixture
async def control(hass: HomeAssistant, local_time, freezer):
    """Profile 'Living room' (day 21-25 °C, auto range) controlling climate.ac."""
    return await setup_control(hass, local_time, freezer)


async def test_entities_and_initial_state(hass: HomeAssistant, control) -> None:
    """A controlled profile gets switch, button, sensors and offsets."""
    entry, calls = control
    reg = er.async_get(hass)
    ids = {
        e.entity_id: e for e in er.async_entries_for_config_entry(reg, entry.entry_id)
    }
    for entity_id in (STATUS, REASON, STATE, SWITCH, BUTTON, OFFSET_HEAT, OFFSET_COOL):
        assert entity_id in ids, entity_id
    diag = [
        e for e in ids.values() if e.entity_category == er.EntityCategory.DIAGNOSTIC
    ]
    assert len(diag) == 8
    assert all(e.disabled_by is er.RegistryEntryDisabler.INTEGRATION for e in diag)
    # The cleanup list for removed controls knows every control entity.
    from custom_components.heating_profile import CONTROL_ENTITY_KEYS

    profile_keys = {
        "climate",
        "day_temp",
        "day_temp_high",
        "night_temp",
        "night_temp_high",
        "day_start",
        "night_start",
    }
    keys = {e.unique_id.removeprefix(f"{entry.entry_id}_") for e in ids.values()}
    assert keys - profile_keys == set(CONTROL_ENTITY_KEYS)
    assert st(hass, SWITCH) == "on"
    assert st(hass, STATE) == "idle"
    assert st(hass, STATUS) == "In range 21.0–25.0 °C – fan only"
    assert st(hass, REASON) == ""
    assert st(hass, OFFSET_HEAT) == "2.0"
    assert sent(calls) == []  # AC already fan_only/silent


async def test_profile_without_control_has_no_control_entities(
    hass: HomeAssistant, local_time
) -> None:
    """Without room sensor and AC nothing changes compared to before."""
    entry = MockConfigEntry(domain=DOMAIN, title="Living room", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.controller is None
    assert hass.states.get(STATUS) is None and hass.states.get(SWITCH) is None


async def test_cooling_run(hass: HomeAssistant, freezer, control) -> None:
    """Too warm -> cool to the middle; status stable during the run."""
    entry, calls = control
    hass.states.async_set(ROOM, "25.4")
    await advance(hass, freezer, 9)
    assert st(hass, STATE) == "idle"  # 10-min average still below 25.3
    await advance(hass, freezer, 1)
    assert st(hass, STATE) == "cooling"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "cool"}),
        ("set_temperature", {"temperature": 21.0}),  # stop 23 - offset 2
        ("set_fan_mode", {"fan_mode": "auto"}),
    ]
    assert st(hass, STATUS) == "Cooling to 23.0 °C"
    assert st(hass, REASON) == (
        "Too warm (25.4 °C, maximum 25.0 °C), no cooler outside air expected"
    )
    set_ac(hass, "cool", 21.0, "auto")  # the AC follows
    await hass.async_block_till_done()
    since = hass.states.get(STATUS).last_changed
    hass.states.async_set(ROOM, "24.0")
    await advance(hass, freezer, 12)
    assert st(hass, STATE) == "cooling" and sent(calls) == []
    assert hass.states.get(STATUS).last_changed == since  # no live values
    assert st(hass, REASON).startswith("Too warm (25.4")  # stays during the run
    hass.states.async_set(ROOM, "22.9")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "idle"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "fan_only"}),
        ("set_fan_mode", {"fan_mode": "silent"}),
    ]
    assert st(hass, REASON) == ""
    set_ac(hass, "fan_only", 21.0, "silent")
    await hass.async_block_till_done()
    assert controller(entry).state.cool_ended is not None


async def test_minimum_run_time(hass: HomeAssistant, freezer, control) -> None:
    """A run reaching the stop point early keeps going until the minimum."""
    entry, calls = control
    hass.states.async_set(ROOM, "19.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    # The 10-min average passes the normal start point first.
    assert st(hass, REASON).startswith("Too cold (20."), st(hass, REASON)
    started = controller(entry).state.last_switch
    set_ac(hass, "heat", 25.0, "auto")
    hass.states.async_set(ROOM, "24.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    assert (
        st(hass, STATUS)
        == f"Heating – 23.0 °C reached, minimum run until {hm(started + 1200)}"
    )
    await advance(hass, freezer, 11)
    assert st(hass, STATE) == "idle"


async def test_lockout_min_pause_and_hard_limit(
    hass: HomeAssistant, freezer, control
) -> None:
    """After cooling, a normal heat start waits for the lockout."""
    entry, calls = control
    c = controller(entry)
    now = dt_util.utcnow().timestamp()
    c.state.cool_ended = now - 3600
    c.state.last_switch = now - 3600
    hass.states.async_set(ROOM, "20.6")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "waiting"
    assert (
        st(hass, STATUS)
        == f"Too cold – locked after cooling until {hm(now + 5 * 3600)}"
    )
    assert sent(calls) == []
    hass.states.async_set(ROOM, "19.4")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    assert st(hass, REASON).startswith("Hard limit: room 19."), st(hass, REASON)
    assert ("set_temperature", {"temperature": 25.0}) in sent(calls)  # 23 + 2
    # Minimum pause after the run.
    set_ac(hass, "heat", 25.0, "auto")
    hass.states.async_set(ROOM, "23.5")
    await advance(hass, freezer, 25)
    assert st(hass, STATE) == "idle"
    ended = c.state.last_switch
    set_ac(hass, "fan_only", 25.0, "silent")
    hass.states.async_set(ROOM, "20.5")
    await advance(hass, freezer, 10)
    assert st(hass, STATUS) == f"Too cold – minimum pause until {hm(ended + 1200)}"


async def test_waiting_for_warmth(hass: HomeAssistant, freezer, control) -> None:
    """Warm enough outside the whole next hour -> wait, at most 60 min."""
    entry, calls = control
    stub = StubForecast()
    controller(entry).forecast = stub
    stub.value = ForecastWindow(min_temp=22.0, max_temp=23.0, radiation=0.0)
    hass.states.async_set(ROOM, "20.8")
    await advance(hass, freezer, 32)
    hass.states.async_set(ROOM, "20.6")  # slow: -0.2 in 30 min
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "waiting"
    wait_since = controller(entry).state.wait_since
    assert st(hass, STATUS) == (
        f"Too cold – waiting for sun or warmth until {hm(wait_since + 3600)}"
    )
    since = hass.states.get(STATUS).last_changed
    await advance(hass, freezer, 30)
    assert hass.states.get(STATUS).last_changed == since
    assert sent(calls) == []
    await advance(hass, freezer, 25)
    assert st(hass, STATE) == "heating"
    assert st(hass, REASON).endswith("waited 60 min for sun or warmth")


async def test_no_waiting_when_falling_fast_or_forecast_too_cold(
    hass: HomeAssistant, freezer, control
) -> None:
    """Fast fall or outside not warm enough the whole hour -> heat."""
    entry, calls = control
    stub = StubForecast()
    controller(entry).forecast = stub
    stub.value = ForecastWindow(min_temp=15.0, max_temp=25.0, radiation=50.0)
    hass.states.async_set(ROOM, "20.8")
    await advance(hass, freezer, 32)
    hass.states.async_set(ROOM, "20.5")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    assert st(hass, REASON).endswith("no sun or warmth expected")


async def test_manual_change_pauses(hass: HomeAssistant, freezer, control) -> None:
    """A change the control didn't send pauses it; the button resumes."""
    entry, calls = control
    hass.states.async_set(ROOM, "19.0")
    await advance(hass, freezer, 10)
    sent(calls)
    set_ac(hass, "heat", 25.0, "auto")  # echo of the command: no pause
    await hass.async_block_till_done()
    assert st(hass, STATE) == "heating"
    await advance(hass, freezer, 3)
    set_ac(hass, "heat", 28.0, "high")  # the remote
    await hass.async_block_till_done()
    assert st(hass, STATE) == "paused"
    until = controller(entry).state.pause_until
    assert st(hass, STATUS) == f"Paused until {hm(until)} (manual change on the AC)"
    hass.states.async_set(ROOM, "23.5")
    await advance(hass, freezer, 30)
    assert sent(calls) == []
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: BUTTON}, blocking=True
    )
    await hass.async_block_till_done()
    # Resumes at once: room 23.5 is past the stop point, minimum run is over.
    assert st(hass, STATE) == "idle"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "fan_only"}),
        ("set_fan_mode", {"fan_mode": "silent"}),
    ]
    # The control's own commands never cause a pause.
    set_ac(hass, "fan_only", 28.0, "silent")
    await advance(hass, freezer, 5)
    assert st(hass, STATE) == "idle"


async def test_pause_expires(hass: HomeAssistant, freezer, control) -> None:
    """After the pause the control takes over again."""
    entry, calls = control
    await advance(hass, freezer, 1)
    set_ac(hass, "fan_only", 20.0, "silent")
    controller(entry).state.last_command = {
        "mode": "fan_only",
        "temp": None,
        "fan": "silent",
    }
    controller(entry).state.last_command_at = dt_util.utcnow().timestamp() - 1000
    set_ac(hass, "off", 20.0, "silent")  # switched off with the remote
    await hass.async_block_till_done()
    assert st(hass, STATE) == "paused"
    await advance(hass, freezer, 119)
    assert sent(calls) == []
    await advance(hass, freezer, 2)
    assert st(hass, STATE) == "idle"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "fan_only"}),
        ("set_fan_mode", {"fan_mode": "silent"}),
    ]


async def test_switch_off_and_on(hass: HomeAssistant, freezer, control) -> None:
    """Off: nothing is sent. On again: ends a pause and evaluates."""
    entry, calls = control
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    await hass.async_block_till_done()
    assert st(hass, STATE) == "disabled" and st(hass, STATUS) == "Control off"
    hass.states.async_set(ROOM, "19.0")
    await advance(hass, freezer, 15)
    assert sent(calls) == []
    controller(entry).state.pause_until = dt_util.utcnow().timestamp() + 3600
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    await hass.async_block_till_done()
    assert st(hass, STATE) == "heating"
    assert controller(entry).state.pause_until is None


async def test_presence(hass: HomeAssistant, freezer, control) -> None:
    """Away long enough: AC off while idle; runs still happen; back: fan."""
    entry, calls = control
    hass.states.async_set(PRES, "off")
    await advance(hass, freezer, 59)
    assert st(hass, STATE) == "idle" and sent(calls) == []
    left = controller(entry).state.last_present
    await advance(hass, freezer, 1)
    assert st(hass, STATE) == "away"
    assert st(hass, STATUS) == f"Away since {hm(left)} – AC off"
    assert sent(calls) == [("set_hvac_mode", {"hvac_mode": "off"})]
    set_ac(hass, "off", 20.0, "silent")
    # Too cold while away: heats, then off again (not fan).
    hass.states.async_set(ROOM, "19.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    assert ("set_hvac_mode", {"hvac_mode": "heat"}) in sent(calls)
    set_ac(hass, "heat", 25.0, "auto")
    hass.states.async_set(ROOM, "23.5")
    await advance(hass, freezer, 25)
    assert st(hass, STATE) == "away"
    assert sent(calls) == [("set_hvac_mode", {"hvac_mode": "off"})]
    set_ac(hass, "off", 25.0, "auto")
    # Back home: straight back to fan only.
    hass.states.async_set(PRES, "on")
    await hass.async_block_till_done()
    assert st(hass, STATE) == "idle"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "fan_only"}),
        ("set_fan_mode", {"fan_mode": "silent"}),
    ]
    # Unavailable presence counts as present.
    set_ac(hass, "fan_only", 25.0, "silent")
    hass.states.async_set(PRES, "unavailable")
    await advance(hass, freezer, 90)
    assert st(hass, STATE) == "idle"


async def test_profile_off_switches_ac_off(
    hass: HomeAssistant, freezer, control
) -> None:
    """Profile off = AC off; back on = fan only."""
    entry, calls = control
    await set_profile_mode(hass, entry, "off")
    assert st(hass, STATE) == "off" and st(hass, STATUS) == "Profile off – AC off"
    assert sent(calls) == [("set_hvac_mode", {"hvac_mode": "off"})]
    set_ac(hass, "off", 20.0, "silent")
    hass.states.async_set(ROOM, "30.0")
    await advance(hass, freezer, 15)
    assert st(hass, STATE) == "off" and sent(calls) == []
    hass.states.async_set(ROOM, "23.0")
    await advance(hass, freezer, 15)
    await set_profile_mode(hass, entry, "heat_cool")
    assert st(hass, STATE) == "idle"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "fan_only"}),
        ("set_fan_mode", {"fan_mode": "silent"}),
    ]


async def test_offset_tuning(hass: HomeAssistant, freezer, control) -> None:
    """Compressor idle while short -> +0.5; overshoot after a run -> -0.5."""
    entry, calls = control
    c = controller(entry)
    hass.states.async_set(ROOM, "19.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    set_ac(hass, "heat", 25.0, "auto")
    sent(calls)
    await advance(hass, freezer, 5)
    hass.states.async_set(COMP, "off")  # the AC's own sensor says "done"
    await advance(hass, freezer, 14)
    assert c.state.offset_heat == 2.0
    await advance(hass, freezer, 1)
    assert c.state.offset_heat == 2.5
    assert st(hass, OFFSET_HEAT) == "2.5"
    assert ("set_temperature", {"temperature": 25.5}) in sent(calls)
    set_ac(hass, "heat", 25.5, "auto")
    # Not again within 15 min.
    await advance(hass, freezer, 14)
    assert c.state.offset_heat == 2.5
    # Run ends at 23; room overshoots to 24.5 afterwards -> -0.5 after 30 min.
    hass.states.async_set(COMP, "on")
    hass.states.async_set(ROOM, "23.2")
    await advance(hass, freezer, 12)
    assert st(hass, STATE) == "idle"
    ended = c.state.heat_ended
    set_ac(hass, "fan_only", 25.5, "silent")
    hass.states.async_set(ROOM, "24.5")
    left = int((ended + 1800 - dt_util.utcnow().timestamp()) // 60)
    await advance(hass, freezer, left - 1)
    assert c.state.offset_heat == 2.5  # checked only 30 min after the run
    await advance(hass, freezer, 2)
    assert c.state.offset_heat == 2.0
    # Offsets can be set by hand.
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: OFFSET_COOL, "value": 3.5},
        blocking=True,
    )
    assert c.state.offset_cool == 3.5


async def test_room_sensor_and_ac_unavailable(
    hass: HomeAssistant, freezer, control
) -> None:
    """No room reading -> idle (fan); AC unavailable -> nothing sent."""
    entry, calls = control
    hass.states.async_set(ROOM, "19.0")
    await advance(hass, freezer, 10)
    set_ac(hass, "heat", 25.0, "auto")
    sent(calls)
    hass.states.async_set(ROOM, "unavailable")
    await advance(hass, freezer, 1)
    assert st(hass, STATE) == "unavailable"
    assert st(hass, STATUS) == "Room sensor unavailable – fan only"
    assert ("set_hvac_mode", {"hvac_mode": "fan_only"}) in sent(calls)
    hass.states.async_set(AC, "unavailable")
    await advance(hass, freezer, 3)
    assert st(hass, STATUS) == "AC unavailable" and sent(calls) == []


async def test_state_survives_reload(hass: HomeAssistant, freezer, control) -> None:
    """Run, offsets and times are stored."""
    entry, calls = control
    hass.states.async_set(ROOM, "19.0")
    await advance(hass, freezer, 10)
    set_ac(hass, "heat", 25.0, "auto")
    controller(entry).state.offset_cool = 3.0
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    c = controller(entry)
    assert c.state.run == "heating" and c.state.offset_cool == 3.0
    assert st(hass, REASON).startswith("Too cold (")


async def test_unsupported_idle_mode_warns_once(
    hass: HomeAssistant, freezer, local_time, caplog
) -> None:
    """An idle mode the AC doesn't know is not sent; one warning."""
    caplog.set_level(logging.WARNING)
    entry, calls = await setup_control(
        hass, local_time, freezer, {**OPTIONS, "idle_hvac_mode": "fan"}
    )
    await advance(hass, freezer, 5)
    assert sent(calls) == []
    assert caplog.text.count("does not support mode fan") == 1


async def test_forecast_source(hass: HomeAssistant, aioclient_mock) -> None:
    """Open-Meteo hourly data -> window; errors keep the old data."""
    from custom_components.heating_profile.controller import ForecastSource

    now = dt_util.utcnow().timestamp()
    hour = int(now // 3600 * 3600)
    times = [hour + i * 3600 for i in range(-1, 5)]
    aioclient_mock.get(
        "https://api.open-meteo.com/v1/forecast",
        json={
            "hourly": {
                "time": times,
                "temperature_2m": [10, 12, 14, 16, 18, 20],
                "shortwave_radiation": [0, 100, 200, 300, 400, 500],
            }
        },
    )
    src = ForecastSource(hass)
    assert src.window(now, 60) is None
    await src.async_refresh()
    w = src.window(now, 60)
    assert w is not None and w.min_temp < w.max_temp and w.radiation > 0
    params = aioclient_mock.mock_calls[0][1].query
    assert params["hourly"] == "temperature_2m,shortwave_radiation"
    assert params["timeformat"] == "unixtime"
    # Stale after 3 h.
    assert src.window(now + 3 * 3600 + 1, 60) is None
    # A failing request keeps the previous data.
    aioclient_mock.clear_requests()
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", status=500)
    await src.async_refresh()
    assert src.data is not None and src.window(now, 60) is not None
    aioclient_mock.clear_requests()
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", json={"x": 1})
    await src.async_refresh()
    assert src.window(now, 60) is not None


async def test_forecast_used_after_setup(
    hass: HomeAssistant, local_time, freezer, aioclient_mock
) -> None:
    """With the forecast on, it is fetched at setup and used for waiting."""
    local_time(12)
    now = dt_util.utcnow().timestamp()
    hour = int(now // 3600 * 3600)
    aioclient_mock.get(
        "https://api.open-meteo.com/v1/forecast",
        json={
            "hourly": {
                "time": [hour + i * 3600 for i in range(-1, 30)],
                "temperature_2m": [23.0] * 31,
                "shortwave_radiation": [0.0] * 31,
            }
        },
    )
    entry, calls = await setup_control(
        hass, local_time, freezer, {**OPTIONS, "use_forecast": True}
    )
    assert aioclient_mock.call_count == 1
    hass.states.async_set(ROOM, "20.8")
    await advance(hass, freezer, 32)
    hass.states.async_set(ROOM, "20.6")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "waiting"  # 23 °C outside >= 21 + 1
    assert (
        hass.states.get("sensor.living_room_forecast_outside_minimum") is None
    )  # disabled


async def test_remove_entry_deletes_control_storage(
    hass: HomeAssistant, control, hass_storage
) -> None:
    """Removing the profile removes the stored control state."""
    entry, calls = control
    key = f"{DOMAIN}.{entry.entry_id}.control"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert key in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_corrupt_control_storage(
    hass: HomeAssistant, local_time, freezer, hass_storage
) -> None:
    """Broken stored control state falls back to defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living room", data={}, options=OPTIONS
    )
    hass_storage[f"{DOMAIN}.{entry.entry_id}.control"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}.control",
        "data": {
            "run": "dancing",
            "offset_heat": "hot",
            "offset_cool": -3,
            "last_command": {"mode": "heat"},
            "last_cycle": {"kind": "x"},
            "enabled": True,
        },
    }
    local_time(12)
    hass.states.async_set(ROOM, "23.0")
    set_ac(hass, "fan_only")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = controller(entry)
    assert c.state.run == "idle"
    assert c.state.offset_heat == 2.0 and c.state.offset_cool == 2.0
    assert c.state.last_command is None and c.state.last_cycle is None


# ----- look ahead to the next period (default day 06:00-22:00) -----------


async def test_look_ahead_skips_evening_heating(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """30 min before night (min 17) no heating for the day minimum (21)."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=21)
    await advance(hass, freezer, 19)  # 21:31
    hass.states.async_set(ROOM, "20.5")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "idle" and sent(calls) == []
    assert st(hass, STATUS) == ("In range 17.0–24.0 °C (night from 22:00) – fan only")
    assert hass.states.get(STATUS).attributes["look_ahead"] == "night from 22:00"


async def test_look_ahead_off_heats_for_current_period(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """Look-ahead 0: the current (day) minimum still applies."""
    entry, calls = await setup_control(
        hass, local_time, freezer, {**OPTIONS, "look_ahead": 0}, hour=21
    )
    await advance(hass, freezer, 19)
    hass.states.async_set(ROOM, "20.5")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    assert "night" not in st(hass, REASON)


async def test_look_ahead_precools_for_the_night(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """Night maximum 22: from 21:30 it cools to the night's middle."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=21)
    entry.runtime_data.profile.async_update(night_temp_high=22.0)
    await advance(hass, freezer, 18)  # 21:30
    assert st(hass, STATE) == "cooling"  # 23 >= 22.3
    assert ("set_temperature", {"temperature": 17.5}) in sent(calls)  # 19.5 - 2
    assert st(hass, STATUS) == "Cooling to 19.5 °C (night from 22:00)"
    assert st(hass, REASON).endswith("(night from 22:00)")


async def test_look_ahead_preheats_in_the_morning(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """20 °C is fine at night (17-24) but from 05:30 the day (21) counts."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=5)
    hass.states.async_set(ROOM, "20.0")
    await advance(hass, freezer, 12)  # 05:24
    assert st(hass, STATE) == "idle"
    await advance(hass, freezer, 6)  # 05:30
    assert st(hass, STATE) == "heating"
    assert st(hass, REASON) == (
        "Too cold (20.0 °C, minimum 21.0 °C), no sun or warmth expected"
        " (day from 06:00)"
    )


async def test_look_ahead_ends_a_run_at_the_next_middle(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """A day heating run stops at the night's middle once look-ahead starts."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=21)
    hass.states.async_set(ROOM, "19.0")
    await advance(hass, freezer, 7)  # 21:19
    assert st(hass, STATE) == "heating"
    set_ac(hass, "heat", 25.0, "auto")
    hass.states.async_set(ROOM, "21.0")
    await advance(hass, freezer, 10)  # 21:29: day middle 23 not reached
    assert st(hass, STATE) == "heating"
    started = controller(entry).state.last_switch
    await advance(hass, freezer, 2)  # 21:31: night middle 20.5 reached
    assert st(hass, STATUS) == (
        f"Heating – 20.5 °C reached, minimum run until {hm(started + 1200)}"
        " (night from 22:00)"
    )
    left = int((started + 1200 - dt_util.utcnow().timestamp()) // 60)
    await advance(hass, freezer, left)
    assert st(hass, STATE) == "idle"


async def test_look_ahead_respects_manual_override(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """Night forced by hand until 22:00: the night stays, nothing changes."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=20)
    profile = entry.runtime_data.profile
    profile.async_set_period("night", dt_util.utcnow())
    await hass.async_block_till_done()
    await advance(hass, freezer, 80)  # 21:32
    assert st(hass, STATUS) == "In range 17.0–24.0 °C – fan only"
