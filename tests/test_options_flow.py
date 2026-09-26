"""Options flow (climate control settings)."""

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_profile.config_flow import NUMBERS, SECTIONS
from custom_components.heating_profile.const import (
    CONTROL_DEFAULTS,
    DOMAIN,
    SOURCE_KEYS,
)

AC = "climate.ac"
SOURCES = {
    "room_sensor": "sensor.room",
    "ac_entity": AC,
    "power_sensor": "sensor.ac_power",
    "use_forecast": False,
}


def _ac(hass: HomeAssistant) -> None:
    hass.states.async_set(
        AC,
        "fan_only",
        {
            "hvac_modes": ["off", "auto", "cool", "dry", "heat", "fan_only"],
            "fan_modes": ["silent", "low", "auto"],
        },
    )
    hass.states.async_set("sensor.room", "22", {"device_class": "temperature"})
    hass.states.async_set("sensor.ac_power", "5", {"device_class": "power"})


async def _entry(hass: HomeAssistant, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living room", data={}, options=options or {}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _defaults(schema) -> dict:
    values = {}
    for sec in schema.values():
        for key in sec.schema.schema:
            values[str(key)] = key.default()
    return values


def _sections(values: dict) -> dict:
    return {name: {k: values[k] for k in keys} for name, keys in SECTIONS.items()}


def test_every_setting_has_a_default_label_and_description() -> None:
    """Sections, defaults, number ranges and translations fit together."""
    keys = [k for keys in SECTIONS.values() for k in keys]
    assert len(keys) == len(set(keys))
    modes = {"idle_hvac_mode", "idle_fan_mode", "active_fan_mode", "standby_fan_mode"}
    assert set(keys) - modes == set(CONTROL_DEFAULTS) - {"use_forecast"}
    for key, value in CONTROL_DEFAULTS.items():
        if key in NUMBERS:
            low, high, _step, _unit = NUMBERS[key]
            assert low <= value <= high, key
    strings = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components/heating_profile/translations/en.json"
        ).read_text()
    )["options"]["step"]
    for name, sec_keys in SECTIONS.items():
        sec = strings["settings"]["sections"][name]
        assert sec["name"] and sec["description"]
        assert set(sec["data"]) == set(sec_keys) == set(sec["data_description"])
    init = strings["init"]
    assert set(init["data"]) == set(SOURCE_KEYS) | {"use_forecast"}
    assert set(init["data_description"]) == set(init["data"])


async def test_full_flow_sets_up_control(hass: HomeAssistant, local_time) -> None:
    """Sources -> settings with defaults -> control entities appear."""
    local_time(12)
    _ac(hass)
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SOURCES
    )
    assert result["step_id"] == "settings"
    schema = result["data_schema"].schema
    defaults = _defaults(schema)
    assert defaults["target_margin"] == 0.3 and defaults["idle_exit"] == 60
    assert defaults["exit_warmth_margin"] == 2.0 and defaults["exit_window"] == 120
    assert defaults["idle_hvac_mode"] == "fan_only"
    assert defaults["idle_fan_mode"] == "silent"
    assert defaults["active_fan_mode"] == "auto"
    assert defaults["standby_fan_mode"] == "silent"
    # Idle modes offered: the AC's modes without off/heat/cool.
    ac_sec = schema[next(k for k in schema if str(k) == "ac_modes")]
    idle_key = next(k for k in ac_sec.schema.schema if str(k) == "idle_hvac_mode")
    assert ac_sec.schema.schema[idle_key].config["options"] == [
        "auto",
        "dry",
        "fan_only",
    ]
    values = {**defaults, "exit_warmth_margin": 4.5, "learn_gain": 0.3}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _sections(values)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["exit_warmth_margin"] == 4.5
    assert entry.options["room_sensor"] == "sensor.room"
    assert entry.options["power_sensor"] == "sensor.ac_power"
    assert "compressor_sensor" not in entry.options
    c = entry.runtime_data.controller
    assert c is not None and c.settings["learn_gain"] == 0.3
    assert hass.states.get("switch.living_room_climate_control") is not None


async def test_incomplete_sources(hass: HomeAssistant, local_time) -> None:
    """Room sensor and AC together; an activity sensor is required."""
    local_time(12)
    _ac(hass)
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"room_sensor": "sensor.room"}
    )
    assert result["errors"] == {"base": "control_incomplete"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"room_sensor": "sensor.room", "ac_entity": AC}
    )
    assert result["errors"] == {"base": "activity_missing"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "room_sensor": "sensor.room",
            "ac_entity": AC,
            "compressor_sensor": "binary_sensor.c",
        },
    )
    assert result["step_id"] == "settings"


async def test_settings_validation(hass: HomeAssistant, local_time) -> None:
    """Offset range and power thresholds must make sense."""
    local_time(12)
    _ac(hass)
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SOURCES
    )
    defaults = _defaults(result["data_schema"].schema)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _sections({**defaults, "offset_min": 5.0, "offset_max": 4.0})
    )
    assert result["errors"] == {"base": "offset_range"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _sections({**defaults, "high_power": 40})
    )
    assert result["errors"] == {"base": "power_thresholds"}


async def test_clearing_sources_removes_control(
    hass: HomeAssistant, local_time
) -> None:
    """Empty sources: no control; the other settings are kept for later."""
    local_time(12)
    _ac(hass)
    entry = await _entry(hass, {**SOURCES, "idle_exit": 45})
    assert entry.runtime_data.controller is not None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"use_forecast": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.controller is None
    assert "ac_entity" not in entry.options and entry.options["idle_exit"] == 45
    assert hass.states.get("switch.living_room_climate_control") is None


async def test_flow_over_http_api(hass: HomeAssistant, hass_client) -> None:
    """Both steps serialize for the frontend (sections, selectors)."""
    # Real clock: the frozen test clock would make the admin token look
    # issued in the future.
    _ac(hass)
    entry = await _entry(hass)
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    resp = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    assert resp.status == 200, await resp.text()
    data = await resp.json()
    assert data["step_id"] == "init"
    names = [f["name"] for f in data["data_schema"]]
    assert names == [
        "room_sensor",
        "ac_entity",
        "power_sensor",
        "compressor_sensor",
        "use_forecast",
    ]
    resp = await client.post(
        f"/api/config/config_entries/options/flow/{data['flow_id']}", json=SOURCES
    )
    assert resp.status == 200, await resp.text()
    data = await resp.json()
    assert data["step_id"] == "settings"
    sections = {f["name"]: f for f in data["data_schema"]}
    assert list(sections) == list(SECTIONS)
    assert sections["ac_modes"]["type"] == "expandable"
    assert sections["ac_modes"]["expanded"] is True
    assert sections["learning"]["expanded"] is False
    fields = {f["name"]: f for f in sections["mode_changes"]["schema"]}
    assert fields["idle_exit"]["default"] == 60
    assert fields["idle_exit"]["selector"]["number"]["unit_of_measurement"] == "min"
    payload = {
        name: {f["name"]: f["default"] for f in sec["schema"]}
        for name, sec in sections.items()
    }
    resp = await client.post(
        f"/api/config/config_entries/options/flow/{data['flow_id']}", json=payload
    )
    assert resp.status == 200, await resp.text()
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["target_margin"] == 0.3 and entry.options["ac_entity"] == AC
