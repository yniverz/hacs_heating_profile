"""Climate control scenarios against a real Home Assistant core.

The AC is simulated: its state is set by the tests, its services are mocked
and recorded. Profile: day 06:00-22:00 with 21-25 °C, night 17-24 °C.
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
POWER = "sensor.ac_power"
COMP = "binary_sensor.compressor"
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
    "power_sensor": POWER,
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


def set_power(hass: HomeAssistant, watts: float) -> None:
    hass.states.async_set(POWER, str(watts), {"unit_of_measurement": "W"})


class StubForecast:
    """Stands in for the Open-Meteo source: one window per length."""

    def __init__(self) -> None:
        self.wait: ForecastWindow | None = None  # 60 min
        self.exit: ForecastWindow | None = None  # 120 min

    def window(self, now: float, minutes: float) -> ForecastWindow | None:
        return self.exit if minutes == 120 else self.wait


def controller(entry: MockConfigEntry):
    return entry.runtime_data.controller


def stub_forecast(entry: MockConfigEntry) -> StubForecast:
    stub = StubForecast()
    controller(entry).forecast = stub
    return stub


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
    set_power(hass, 5)
    hass.states.async_set(COMP, "off")
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


async def start_heating(hass: HomeAssistant, freezer, calls) -> None:
    """Room 21.0 -> heat mode after the 10-min average; the AC follows."""
    hass.states.async_set(ROOM, "21.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "heat"}),
        ("set_temperature", {"temperature": 23.5}),  # 21.3 + 2 -> 23.5
        ("set_fan_mode", {"fan_mode": "silent"}),  # compressor not running yet
    ]
    set_ac(hass, "heat", 23.5, "silent")
    # The compressor starts: fan mode right away.
    set_power(hass, 250)
    hass.states.async_set(COMP, "on")
    await hass.async_block_till_done()
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "auto"})]
    set_ac(hass, "heat", 23.5, "auto")
    await hass.async_block_till_done()


@pytest.fixture
async def control(hass: HomeAssistant, local_time, freezer):
    """Profile 'Living room' (day 21-25 °C, auto range) controlling climate.ac."""
    return await setup_control(hass, local_time, freezer)


async def test_entities_and_initial_state(hass: HomeAssistant, control) -> None:
    """A controlled profile gets switch, button, sensors and offsets."""
    from custom_components.heating_profile import CONTROL_ENTITY_KEYS

    entry, calls = control
    reg = er.async_get(hass)
    ids = {
        e.entity_id: e for e in er.async_entries_for_config_entry(reg, entry.entry_id)
    }
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
    diag = [
        e for e in ids.values() if e.entity_category == er.EntityCategory.DIAGNOSTIC
    ]
    assert len(diag) == 8
    assert all(e.disabled_by is er.RegistryEntryDisabler.INTEGRATION for e in diag)
    assert st(hass, SWITCH) == "on"
    assert st(hass, STATE) == "neutral"
    assert st(hass, STATUS) == "In range 21.0–25.0 °C – fan only"
    assert st(hass, REASON) == ""
    assert st(hass, OFFSET_HEAT) == "2.0"
    assert sent(calls) == []  # AC already fan_only/silent


async def test_climate_current_temperature_is_the_room_sensor(
    hass: HomeAssistant, control
) -> None:
    """The profile's climate entity shows the room sensor as current temperature."""
    assert hass.states.get(PROFILE).attributes["current_temperature"] == 23.0
    hass.states.async_set(ROOM, "21.46", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    # Climate entities show tenths (Home Assistant rounds to the precision).
    assert hass.states.get(PROFILE).attributes["current_temperature"] == 21.5
    hass.states.async_set(ROOM, "71.6", {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    assert hass.states.get(PROFILE).attributes["current_temperature"] == 22.0
    hass.states.async_set(ROOM, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(PROFILE).attributes["current_temperature"] is None


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
    assert hass.states.get(PROFILE).attributes["current_temperature"] is None


async def test_heat_mode_holds_the_target(
    hass: HomeAssistant, freezer, control
) -> None:
    """Near the minimum -> heat mode; the AC regulates to target + offset."""
    entry, calls = control
    hass.states.async_set(ROOM, "21.2")
    await advance(hass, freezer, 15)
    assert st(hass, STATE) == "neutral"  # 21.2 > 21.1
    await start_heating(hass, freezer, calls)
    assert st(hass, STATUS) == "Heating mode – holding 21.3 °C"
    # The reason shows the 10-min average when the mode started.
    assert st(hass, REASON) == (
        "Near minimum (21.1 °C, minimum 21.0 °C), no sun or warmth expected"
    )
    attrs = hass.states.get(STATUS).attributes
    assert attrs["target"] == pytest.approx(21.3) and attrs["ac_setpoint"] == 23.5
    since = hass.states.get(STATUS).last_changed
    # The AC works; the room settles. Nothing else happens.
    hass.states.async_set(ROOM, "21.4")
    set_power(hass, 140)
    await advance(hass, freezer, 60)
    assert st(hass, STATE) == "heating" and sent(calls) == []
    assert hass.states.get(STATUS).last_changed == since


async def test_idle_exit(hass: HomeAssistant, freezer, control) -> None:
    """The AC idles 60 min (after at least 2 h in the mode) -> neutral."""
    entry, calls = control
    await start_heating(hass, freezer, calls)
    started = controller(entry).state.mode_since
    hass.states.async_set(ROOM, "21.5")
    set_power(hass, 140)
    await advance(hass, freezer, 70)
    set_power(hass, 5)  # AC idles from here
    await hass.async_block_till_done()
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "silent"})]
    set_ac(hass, "heat", 23.5, "silent")
    await advance(hass, freezer, 59)
    assert st(hass, STATE) == "heating"  # idle 59 min
    await advance(hass, freezer, 2)
    assert st(hass, STATE) == "neutral"
    assert controller(entry).state.heat_ended - started >= 2 * 3600
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "fan_only"}),
        ("set_fan_mode", {"fan_mode": "silent"}),
    ]
    assert st(hass, REASON) == ""


async def test_idle_exit_waits_for_minimum_time(
    hass: HomeAssistant, freezer, control
) -> None:
    """An idle AC right after the start doesn't end the mode before 2 h."""
    entry, calls = control
    await start_heating(hass, freezer, calls)
    hass.states.async_set(ROOM, "21.5")
    set_power(hass, 5)
    await advance(hass, freezer, 110)
    assert st(hass, STATE) == "heating"
    await advance(hass, freezer, 12)
    assert st(hass, STATE) == "neutral"


async def test_learning_raises_and_lowers_the_offset(
    hass: HomeAssistant, freezer, control
) -> None:
    """Too cold at low power -> raise; too warm while working -> lower."""
    entry, calls = control
    c = controller(entry)
    await start_heating(hass, freezer, calls)
    hass.states.async_set(ROOM, "20.9")  # 0.4 below the target
    set_power(hass, 140)  # works gently: its own sensor says "almost there"
    await advance(hass, freezer, 29)
    assert c.state.offset_heat == 2.0  # settle time after the start
    await advance(hass, freezer, 2)
    assert c.state.offset_heat == pytest.approx(2.2)
    assert st(hass, OFFSET_HEAT) == "2.2"
    await advance(hass, freezer, 20)
    assert c.state.offset_heat == pytest.approx(2.4)
    await advance(hass, freezer, 20)
    assert c.state.offset_heat == pytest.approx(2.6)
    # 21.3 + 2.6 = 23.9 -> 24.0 is sent.
    assert ("set_temperature", {"temperature": 24.0}) in sent(calls)
    set_ac(hass, "heat", 24.0, "auto")
    # Working hard (warm-up): not learned.
    set_power(hass, 250)
    await advance(hass, freezer, 40)
    assert c.state.offset_heat == pytest.approx(2.6)
    # Too warm while idle: the AC's own cycling, not learned.
    hass.states.async_set(ROOM, "21.9")
    set_power(hass, 5)
    await advance(hass, freezer, 40)
    assert c.state.offset_heat == pytest.approx(2.6)
    # Too warm while working: lowered, one step per 20 min.
    set_power(hass, 140)
    await hass.async_block_till_done()
    assert c.state.offset_heat == pytest.approx(2.3)
    await advance(hass, freezer, 19)
    assert c.state.offset_heat == pytest.approx(2.3)
    await advance(hass, freezer, 1)
    assert c.state.offset_heat == pytest.approx(2.0)
    # Can be set by hand.
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: OFFSET_HEAT, "value": 1.5},
        blocking=True,
    )
    assert c.state.offset_heat == 1.5


async def test_learning_can_be_turned_off(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """auto_tune off: the offset stays."""
    entry, calls = await setup_control(
        hass, local_time, freezer, {**OPTIONS, "auto_tune": False}
    )
    await start_heating(hass, freezer, calls)
    hass.states.async_set(ROOM, "20.9")
    set_power(hass, 140)
    await advance(hass, freezer, 90)
    assert controller(entry).state.offset_heat == 2.0


async def test_early_exit_and_drift(hass: HomeAssistant, freezer, control) -> None:
    """Really warm outside: heat -> neutral early, room may drift 0.3 °C."""
    entry, calls = control
    stub = stub_forecast(entry)
    await start_heating(hass, freezer, calls)
    hass.states.async_set(ROOM, "21.4")
    set_power(hass, 140)
    stub.exit = ForecastWindow(min_temp=27.5, max_temp=30.0, radiation=0.0)
    await advance(hass, freezer, 29)
    assert st(hass, STATE) == "heating"  # switch gap 30 min
    await advance(hass, freezer, 2)
    assert st(hass, STATE) == "neutral"
    c = controller(entry)
    until = c.state.drift_until
    assert st(hass, STATUS) == (
        "In range 21.0–25.0 °C – fan only "
        f"(warm outside: may drift to 20.7 °C until {hm(until)})"
    )
    assert ("set_hvac_mode", {"hvac_mode": "fan_only"}) in sent(calls)
    set_ac(hass, "fan_only", 23.5, "silent")
    stub.exit = None
    # Down to 20.8: still allowed.
    hass.states.async_set(ROOM, "20.8")
    await advance(hass, freezer, 45)
    assert st(hass, STATE) == "neutral"
    # Drift time over -> heat mode again right away (no extra waiting for sun).
    stub.wait = ForecastWindow(min_temp=10.0, max_temp=12.0, radiation=300.0)
    await advance(hass, freezer, 20)
    assert st(hass, STATE) == "heating"
    assert st(hass, REASON).endswith("sun or warmth didn't come within 60 min")


async def test_early_exit_room_falls_below_drift(
    hass: HomeAssistant, freezer, control
) -> None:
    """During the drift, 0.3 °C below the minimum starts heat mode."""
    entry, calls = control
    stub = stub_forecast(entry)
    await start_heating(hass, freezer, calls)
    hass.states.async_set(ROOM, "21.4")
    set_power(hass, 140)
    stub.exit = ForecastWindow(min_temp=27.5, max_temp=30.0, radiation=0.0)
    await advance(hass, freezer, 31)
    assert st(hass, STATE) == "neutral"
    set_ac(hass, "fan_only", 23.5, "silent")
    stub.exit = None
    hass.states.async_set(ROOM, "20.6")
    await advance(hass, freezer, 20)
    assert st(hass, STATE) == "waiting"  # 30 min gap after the switch
    await advance(hass, freezer, 11)
    assert st(hass, STATE) == "heating"


async def test_waiting_for_sun(hass: HomeAssistant, freezer, control) -> None:
    """Sun coming in the next hour -> wait, at most 60 min."""
    entry, calls = control
    stub = stub_forecast(entry)
    stub.wait = ForecastWindow(min_temp=10.0, max_temp=12.0, radiation=300.0)
    hass.states.async_set(ROOM, "21.3")
    await advance(hass, freezer, 32)
    hass.states.async_set(ROOM, "21.1")  # slow: -0.2 in 30 min
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "waiting"
    wait_since = controller(entry).state.wait_since
    assert st(hass, STATUS) == (
        f"Near minimum – waiting for sun or warmth until {hm(wait_since + 3600)}"
    )
    since = hass.states.get(STATUS).last_changed
    await advance(hass, freezer, 30)
    assert hass.states.get(STATUS).last_changed == since
    assert sent(calls) == []
    await advance(hass, freezer, 29)
    assert st(hass, STATE) == "waiting"
    await advance(hass, freezer, 1)
    assert st(hass, STATE) == "heating"
    assert st(hass, REASON).endswith("waited 60 min for sun or warmth")


async def test_warm_outside_heats_only_at_the_hard_limit(
    hass: HomeAssistant, freezer, control
) -> None:
    """Warmer than max + 1 outside: no heat mode until min - 1.5."""
    entry, calls = control
    stub = stub_forecast(entry)
    stub.wait = ForecastWindow(min_temp=26.5, max_temp=28.0, radiation=0.0)
    hass.states.async_set(ROOM, "20.0")
    await advance(hass, freezer, 12)
    assert st(hass, STATE) == "waiting"
    assert st(hass, STATUS) == (
        "Near minimum – warm outside (above 26.0 °C): heating only below 19.5 °C"
    )
    since = hass.states.get(STATUS).last_changed
    await advance(hass, freezer, 120)  # no time limit
    assert st(hass, STATE) == "waiting" and sent(calls) == []
    assert hass.states.get(STATUS).last_changed == since
    hass.states.async_set(ROOM, "19.4")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    assert st(hass, REASON).startswith("Hard limit: room 19.")


async def test_warm_outside_ends(hass: HomeAssistant, freezer, control) -> None:
    """When the warm forecast goes away, the normal start point applies."""
    entry, calls = control
    stub = stub_forecast(entry)
    stub.wait = ForecastWindow(min_temp=26.5, max_temp=28.0, radiation=0.0)
    hass.states.async_set(ROOM, "20.5")
    await advance(hass, freezer, 30)
    assert st(hass, STATE) == "waiting" and sent(calls) == []
    stub.wait = None
    await advance(hass, freezer, 1)
    assert st(hass, STATE) == "heating"


async def test_cool_mode(hass: HomeAssistant, freezer, control) -> None:
    """Near the maximum -> cool mode holding max - 0.3."""
    entry, calls = control
    hass.states.async_set(ROOM, "24.9")
    await advance(hass, freezer, 12)
    assert st(hass, STATE) == "cooling"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "cool"}),
        ("set_temperature", {"temperature": 22.5}),  # 24.7 - 2 = 22.7 -> 22.5
        ("set_fan_mode", {"fan_mode": "silent"}),  # compressor not running yet
    ]
    set_ac(hass, "cool", 22.5, "silent")
    set_power(hass, 250)
    await hass.async_block_till_done()
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "auto"})]
    assert st(hass, STATUS) == "Cooling mode – holding 24.7 °C"
    assert st(hass, REASON) == (
        "Near maximum (24.9 °C, maximum 25.0 °C), no cooler outside air expected"
    )


async def test_hard_limit_and_lockout(hass: HomeAssistant, freezer, control) -> None:
    """Heat -> cool at the hard limit; afterwards heating is locked out."""
    entry, calls = control
    await start_heating(hass, freezer, calls)
    set_power(hass, 140)
    hass.states.async_set(ROOM, "27.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "cooling"
    assert st(hass, REASON) == (
        "Hard limit: room 27.0 °C, more than 1.5 °C above the maximum 25.0 °C"
    )
    set_ac(hass, "cool", 22.5, "auto")
    hass.states.async_set(ROOM, "24.0")
    set_power(hass, 5)
    await advance(hass, freezer, 190)
    assert st(hass, STATE) == "neutral"
    ended = controller(entry).state.cool_ended
    set_ac(hass, "fan_only", 22.5, "silent")
    hass.states.async_set(ROOM, "21.0")
    await advance(hass, freezer, 40)
    assert st(hass, STATE) == "waiting"
    assert st(hass, STATUS) == (
        f"Near minimum – locked after cooling until {hm(ended + 6 * 3600)}"
    )


async def test_switch_gap(hass: HomeAssistant, freezer, control) -> None:
    """Right after neutral started, heat mode waits for the gap."""
    entry, calls = control
    c = controller(entry)
    c.state.mode_since = dt_util.utcnow().timestamp()
    hass.states.async_set(ROOM, "21.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "waiting"
    assert st(hass, STATUS) == (
        f"Near minimum – heating possible from {hm(c.state.mode_since + 1800)}"
    )
    await advance(hass, freezer, 21)
    assert st(hass, STATE) == "heating"


async def test_manual_change_pauses(hass: HomeAssistant, freezer, control) -> None:
    """A change the control didn't send pauses it; the button resumes."""
    entry, calls = control
    await start_heating(hass, freezer, calls)
    await advance(hass, freezer, 3)
    set_ac(hass, "heat", 28.0, "high")  # the remote
    await hass.async_block_till_done()
    assert st(hass, STATE) == "paused"
    until = controller(entry).state.pause_until
    assert st(hass, STATUS) == f"Paused until {hm(until)} (manual change on the AC)"
    await advance(hass, freezer, 30)
    assert sent(calls) == []
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: BUTTON}, blocking=True
    )
    await hass.async_block_till_done()
    assert st(hass, STATE) == "heating"
    assert sent(calls) == [
        ("set_temperature", {"temperature": 23.5}),
        ("set_fan_mode", {"fan_mode": "auto"}),
    ]
    # The control's own commands never cause a pause.
    set_ac(hass, "heat", 23.5, "auto")
    await advance(hass, freezer, 5)
    assert st(hass, STATE) == "heating"


async def test_pause_expires(hass: HomeAssistant, freezer, control) -> None:
    """After the pause the control takes over again."""
    entry, calls = control
    c = controller(entry)
    c.state.last_command = {"mode": "fan_only", "temp": None, "fan": "silent"}
    c.state.last_command_at = dt_util.utcnow().timestamp() - 1000
    set_ac(hass, "off", 20.0, "silent")  # switched off with the remote
    await hass.async_block_till_done()
    assert st(hass, STATE) == "paused"
    await advance(hass, freezer, 119)
    assert sent(calls) == []
    await advance(hass, freezer, 2)
    assert st(hass, STATE) == "neutral"
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


async def test_measurements_continue_while_control_off(
    hass: HomeAssistant, freezer, control
) -> None:
    """Switched off: nothing is sent, but room average/trend keep updating."""
    entry, calls = control
    reg = er.async_get(hass)
    avg = "sensor.living_room_room_average"
    reg.async_update_entity(avg, disabled_by=None)
    await advance(hass, freezer, 1)  # Home Assistant reloads the entry itself
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    await hass.async_block_till_done()
    await advance(hass, freezer, 1)
    hass.states.async_set(ROOM, "22.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "disabled"
    assert st(hass, avg) == "22.0"
    assert hass.states.get(STATUS).attributes["room"] == 22.0
    assert float(hass.states.get(STATUS).attributes["trend_per_30min"]) < 0
    assert sent(calls) == []


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
    assert st(hass, STATE) == "neutral"
    assert sent(calls) == [
        ("set_hvac_mode", {"hvac_mode": "fan_only"}),
        ("set_fan_mode", {"fan_mode": "silent"}),
    ]


async def test_room_sensor_and_ac_unavailable(
    hass: HomeAssistant, freezer, control
) -> None:
    """No room reading -> neutral (fan); AC unavailable -> nothing sent."""
    entry, calls = control
    await start_heating(hass, freezer, calls)
    hass.states.async_set(ROOM, "unavailable")
    await advance(hass, freezer, 1)
    assert st(hass, STATE) == "unavailable"
    assert st(hass, STATUS) == "Room sensor unavailable – fan only"
    assert ("set_hvac_mode", {"hvac_mode": "fan_only"}) in sent(calls)
    hass.states.async_set(AC, "unavailable")
    await advance(hass, freezer, 3)
    assert st(hass, STATUS) == "AC unavailable" and sent(calls) == []


async def test_compressor_sensor_instead_of_power(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """Without a power sensor the compressor sensor tells idle/working."""
    opts = {k: v for k, v in OPTIONS.items() if k != "power_sensor"}
    entry, calls = await setup_control(
        hass, local_time, freezer, {**opts, "compressor_sensor": COMP}
    )
    await start_heating(hass, freezer, calls)
    hass.states.async_set(ROOM, "21.5")
    await advance(hass, freezer, 125)
    assert st(hass, STATE) == "heating"
    hass.states.async_set(COMP, "off")
    await hass.async_block_till_done()
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "silent"})]
    set_ac(hass, "heat", 23.5, "silent")
    await advance(hass, freezer, 62)
    assert st(hass, STATE) == "neutral"


async def test_fan_follows_the_compressor(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """Compressor sensor first, then power; unknown -> the running fan mode."""
    entry, calls = await setup_control(
        hass,
        local_time,
        freezer,
        {**OPTIONS, "compressor_sensor": COMP},
    )
    await start_heating(hass, freezer, calls)
    # The compressor sensor wins over the power sensor.
    hass.states.async_set(COMP, "off")
    await hass.async_block_till_done()
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "silent"})]
    set_ac(hass, "heat", 23.5, "silent")
    # Power changes don't matter while the compressor sensor is known.
    set_power(hass, 5)
    await advance(hass, freezer, 2)
    set_power(hass, 300)
    await advance(hass, freezer, 2)
    assert sent(calls) == []
    # Compressor sensor unavailable: the power sensor decides.
    hass.states.async_set(COMP, "unavailable")
    await hass.async_block_till_done()
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "auto"})]
    set_ac(hass, "heat", 23.5, "auto")
    set_power(hass, 20)
    await hass.async_block_till_done()
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "silent"})]
    set_ac(hass, "heat", 23.5, "silent")
    # Neither known: the running fan mode.
    hass.states.async_set(POWER, "unknown")
    await hass.async_block_till_done()
    await advance(hass, freezer, 1)
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "auto"})]
    assert st(hass, STATE) == "heating"
    # The configured mode is used.
    set_ac(hass, "heat", 23.5, "auto")
    controller(entry).settings["standby_fan_mode"] = "low"
    hass.states.async_set(COMP, "off")
    await hass.async_block_till_done()
    assert sent(calls) == [("set_fan_mode", {"fan_mode": "low"})]


async def test_state_survives_reload(hass: HomeAssistant, freezer, control) -> None:
    """Mode, offsets and times are stored."""
    entry, calls = control
    await start_heating(hass, freezer, calls)
    controller(entry).state.offset_cool = 3.0
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    c = controller(entry)
    assert c.state.mode == "heat" and c.state.offset_cool == 3.0
    assert st(hass, REASON).startswith("Near minimum (")


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


# ----- look ahead to the next period ---------------------------------------


async def test_look_ahead_skips_evening_heating(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """30 min before night (min 17) no heating for the day minimum (21)."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=21)
    await advance(hass, freezer, 19)  # 21:31
    hass.states.async_set(ROOM, "21.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "neutral" and sent(calls) == []
    assert st(hass, STATUS) == "In range 17.0–24.0 °C (night from 22:00) – fan only"
    assert hass.states.get(STATUS).attributes["look_ahead"] == "night from 22:00"


async def test_look_ahead_off_heats_for_current_period(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """Look-ahead 0: the current (day) minimum still applies."""
    entry, calls = await setup_control(
        hass, local_time, freezer, {**OPTIONS, "look_ahead": 0}, hour=21
    )
    await advance(hass, freezer, 19)
    hass.states.async_set(ROOM, "21.0")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "heating"
    assert "night" not in st(hass, REASON)


async def test_look_ahead_precools_for_the_night(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """Night maximum 22: from 21:30 cool mode holds the night's 21.7."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=21)
    entry.runtime_data.profile.async_update(night_temp_high=22.0)
    await advance(hass, freezer, 18)  # 21:30
    assert st(hass, STATE) == "cooling"  # 23 >= 21.9
    assert ("set_temperature", {"temperature": 19.5}) in sent(calls)  # 21.7 - 2
    assert st(hass, STATUS) == "Cooling mode – holding 21.7 °C (night from 22:00)"
    assert st(hass, REASON).endswith("(night from 22:00)")


async def test_look_ahead_preheats_in_the_morning(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """20 °C is fine at night (17-24) but from 05:30 the day (21) counts."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=5)
    hass.states.async_set(ROOM, "20.0")
    await advance(hass, freezer, 12)  # 05:24
    assert st(hass, STATE) == "neutral"
    await advance(hass, freezer, 6)  # 05:30
    assert st(hass, STATE) == "heating"
    assert ("set_temperature", {"temperature": 23.5}) in sent(calls)
    assert st(hass, REASON) == (
        "Near minimum (20.0 °C, minimum 21.0 °C), no sun or warmth expected"
        " (day from 06:00)"
    )


async def test_look_ahead_moves_the_target(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """In heat mode the target follows the next period from 21:30."""
    entry, calls = await setup_control(hass, local_time, freezer, hour=21)
    await start_heating(hass, freezer, calls)  # 21:22
    await advance(hass, freezer, 7)  # 21:29
    assert sent(calls) == []
    await advance(hass, freezer, 1)  # 21:30
    assert sent(calls) == [("set_temperature", {"temperature": 19.5})]  # 17.3 + 2
    assert st(hass, STATUS) == "Heating mode – holding 17.3 °C (night from 22:00)"


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


# ----- forecast source and storage -----------------------------------------


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
    assert src.window(now + 3 * 3600 + 1, 60) is None  # stale after 3 h
    aioclient_mock.clear_requests()
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", status=500)
    await src.async_refresh()
    assert src.window(now, 60) is not None  # the old data stays
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
                "temperature_2m": [27.0] * 31,
                "shortwave_radiation": [0.0] * 31,
            }
        },
    )
    entry, calls = await setup_control(
        hass, local_time, freezer, {**OPTIONS, "use_forecast": True}
    )
    assert aioclient_mock.call_count == 1
    hass.states.async_set(ROOM, "21.3")
    await advance(hass, freezer, 32)
    hass.states.async_set(ROOM, "21.1")
    await advance(hass, freezer, 10)
    assert st(hass, STATE) == "waiting"  # 27 °C outside > 25 + 1: held
    assert "warm outside (above 26.0 °C)" in st(hass, STATUS)


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


async def test_old_and_corrupt_control_storage(
    hass: HomeAssistant, local_time, hass_storage
) -> None:
    """State from 0.6 or broken values fall back to defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living room", data={}, options=OPTIONS
    )
    hass_storage[f"{DOMAIN}.{entry.entry_id}.control"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}.control",
        "data": {
            "run": "heating",  # 0.6 field, gone
            "mode": "dancing",
            "offset_heat": 2.5,  # carried over
            "offset_cool": "cold",
            "last_command": {"mode": "heat"},
            "drift_side": "sideways",
            "mode_since": "yesterday",
            "enabled": True,
        },
    }
    local_time(12)
    hass.states.async_set(ROOM, "23.0")
    set_power(hass, 5)
    set_ac(hass, "fan_only")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = controller(entry)
    assert c.state.mode == "neutral" and c.state.mode_since is None
    assert c.state.offset_heat == 2.5 and c.state.offset_cool == 2.0
    assert c.state.last_command is None and c.state.drift_side is None


async def test_migrates_old_forecast_margins(
    hass: HomeAssistant, local_time, freezer
) -> None:
    """0.7.0 defaults become the new defaults; own values stay."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living room",
        data={},
        options={
            **OPTIONS,
            "warmth_margin": 1.0,
            "cool_margin": 2.0,
            "exit_warmth_margin": 3.0,
            "exit_cool_margin": 4.5,
        },
        version=1,
        minor_version=1,
    )
    local_time(12)
    hass.states.async_set(ROOM, "23.0")
    set_power(hass, 5)
    set_ac(hass, "fan_only")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 2
    assert entry.options["warmth_margin"] == 1.0
    assert entry.options["cool_margin"] == 1.0
    assert entry.options["exit_warmth_margin"] == 2.0
    assert entry.options["exit_cool_margin"] == 4.5
    assert entry.state is ConfigEntryState.LOADED
