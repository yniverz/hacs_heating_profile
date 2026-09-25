"""Options flow (climate control settings)."""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_profile.const import CONTROL_DEFAULTS, DOMAIN

AC = "climate.ac"


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


async def _entry(hass: HomeAssistant, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Living room", data={}, options=options or {}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _sections(values: dict) -> dict:
    from custom_components.heating_profile.config_flow import SECTIONS

    return {name: {k: values[k] for k in keys} for name, keys in SECTIONS.items()}


async def test_full_flow_sets_up_control(hass: HomeAssistant, local_time) -> None:
    """Sources -> settings with defaults -> control entities appear."""
    local_time(12)
    _ac(hass)
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"room_sensor": "sensor.room", "ac_entity": AC, "use_forecast": False},
    )
    assert result["step_id"] == "settings"
    schema = result["data_schema"].schema
    defaults = {}
    for sec in schema.values():
        for key in sec.schema.schema:
            defaults[str(key)] = key.default()
    assert defaults["min_run"] == 20 and defaults["stop_position"] == 50
    assert defaults["idle_hvac_mode"] == "fan_only"
    assert (
        defaults["idle_fan_mode"] == "silent" and defaults["active_fan_mode"] == "auto"
    )
    # Idle modes offered: the AC's modes without off/heat/cool.
    ac_sec = schema[next(k for k in schema if str(k) == "ac_modes")]
    idle_key = next(k for k in ac_sec.schema.schema if str(k) == "idle_hvac_mode")
    assert ac_sec.schema.schema[idle_key].config["options"] == [
        "auto",
        "dry",
        "fan_only",
    ]
    values = {**defaults, "min_run": 25, "away_after": 90}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _sections(values)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["min_run"] == 25 and entry.options["away_after"] == 90
    assert entry.options["room_sensor"] == "sensor.room"
    assert "compressor_sensor" not in entry.options
    # The entry reloaded with the control.
    c = entry.runtime_data.controller
    assert c is not None and c.settings["min_run"] == 25
    assert hass.states.get("switch.living_room_climate_control") is not None


async def test_incomplete_sources(hass: HomeAssistant, local_time) -> None:
    """Room sensor without AC (or the other way round) is rejected."""
    local_time(12)
    _ac(hass)
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"room_sensor": "sensor.room"}
    )
    assert result["errors"] == {"base": "control_incomplete"}


async def test_hard_limit_below_start(hass: HomeAssistant, local_time) -> None:
    """Hard limit smaller than the start offset is rejected."""
    local_time(12)
    _ac(hass)
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"room_sensor": "sensor.room", "ac_entity": AC}
    )
    values = {
        **CONTROL_DEFAULTS,
        "idle_hvac_mode": "fan_only",
        "idle_fan_mode": "silent",
        "active_fan_mode": "auto",
        "hard_margin": 0.5,
        "start_offset": 1.0,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _sections(values)
    )
    assert result["errors"] == {"base": "hard_below_start"}


async def test_clearing_sources_removes_control(
    hass: HomeAssistant, local_time
) -> None:
    """Empty sources: no control; the other settings are kept for later."""
    local_time(12)
    _ac(hass)
    entry = await _entry(
        hass,
        {
            "room_sensor": "sensor.room",
            "ac_entity": AC,
            "use_forecast": False,
            "min_run": 30,
        },
    )
    assert entry.runtime_data.controller is not None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"use_forecast": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.controller is None
    assert "ac_entity" not in entry.options and entry.options["min_run"] == 30
    assert hass.states.get("switch.living_room_climate_control") is None


async def test_flow_over_http_api(hass: HomeAssistant, hass_client) -> None:
    """Both steps serialize for the frontend (sections, selectors)."""
    # Real clock: the frozen test clock would make the admin token look
    # issued in the future.
    _ac(hass)
    entry = await _entry(hass)
    from homeassistant.setup import async_setup_component

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
        "compressor_sensor",
        "presence_entity",
        "use_forecast",
    ]
    resp = await client.post(
        f"/api/config/config_entries/options/flow/{data['flow_id']}",
        json={"room_sensor": "sensor.room", "ac_entity": AC, "use_forecast": False},
    )
    assert resp.status == 200, await resp.text()
    data = await resp.json()
    assert data["step_id"] == "settings"
    sections = {f["name"]: f for f in data["data_schema"]}
    assert list(sections) == [
        "ac_modes",
        "start_stop",
        "waiting",
        "offsets",
        "manual",
        "presence",
        "measurement",
    ]
    assert sections["ac_modes"]["type"] == "expandable"
    assert sections["ac_modes"]["expanded"] is True
    assert sections["waiting"]["expanded"] is False
    fields = {f["name"]: f for f in sections["start_stop"]["schema"]}
    assert fields["min_run"]["default"] == 20
    assert fields["min_run"]["selector"]["number"]["unit_of_measurement"] == "min"
    # Submit the defaults as the frontend would.
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
    assert entry.options["stop_position"] == 50 and entry.options["ac_entity"] == AC
