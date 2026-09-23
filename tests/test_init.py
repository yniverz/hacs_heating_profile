"""Entity, logic and persistence tests."""

from datetime import time, timedelta

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.number import (
    ATTR_VALUE as NUMBER_ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE as NUMBER_SET_VALUE,
)
from homeassistant.components.time import (
    ATTR_TIME,
    DOMAIN as TIME_DOMAIN,
    SERVICE_SET_VALUE as TIME_SET_VALUE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.heating_profile.const import DOMAIN, SERVICE_SET_PROFILE
from custom_components.heating_profile.profile import is_day

CLIMATE = "climate.living_room"
DAY_TEMP = "number.living_room_day_temperature"
NIGHT_TEMP = "number.living_room_night_temperature"
DAY_START = "time.living_room_day_starts"
NIGHT_START = "time.living_room_night_starts"


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        NUMBER_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, NUMBER_ATTR_VALUE: value},
        blocking=True,
    )


async def set_time(hass: HomeAssistant, entity_id: str, value: str) -> None:
    await hass.services.async_call(
        TIME_DOMAIN,
        TIME_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_TIME: value},
        blocking=True,
    )


async def climate_call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: CLIMATE, **data}, blocking=True
    )


async def tick(hass: HomeAssistant, local_time, hour: int, minute: int = 0) -> None:
    """Move the clock forward to a local time and fire due timers."""
    local_time(hour, minute)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_device_and_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """One device: a climate entity plus four configuration entities."""
    device_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_reg, entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.name == "Living room"
    assert device.manufacturer == "Custom"
    assert device.model == "Heating profile"

    entity_reg = er.async_get(hass)
    entities = {
        e.entity_id: e
        for e in er.async_entries_for_config_entry(entity_reg, entry.entry_id)
    }
    assert set(entities) == {CLIMATE, DAY_TEMP, NIGHT_TEMP, DAY_START, NIGHT_START}
    assert all(e.device_id == device.id for e in entities.values())
    assert entities[CLIMATE].unique_id == f"{entry.entry_id}_climate"
    assert entities[CLIMATE].entity_category is None
    for entity_id in (DAY_TEMP, NIGHT_TEMP, DAY_START, NIGHT_START):
        assert entities[entity_id].entity_category is EntityCategory.CONFIG

    assert hass.states.get(DAY_TEMP).state == "21.0"
    assert hass.states.get(NIGHT_TEMP).state == "17.0"
    assert hass.states.get(DAY_START).state == "06:00:00"
    assert hass.states.get(NIGHT_START).state == "22:00:00"

    day = hass.states.get(DAY_TEMP)
    assert day.attributes["min"] == 5
    assert day.attributes["max"] == 30
    assert day.attributes["step"] == 0.5
    assert day.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    assert day.attributes["device_class"] == "temperature"


async def test_climate_defaults(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The climate entity looks like a thermostat and exposes the profile."""
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.HEAT
    attrs = state.attributes
    assert attrs["hvac_modes"] == [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]
    assert attrs["preset_modes"] == ["day", "night"]
    assert attrs[ATTR_PRESET_MODE] == "day"
    assert attrs[ATTR_TEMPERATURE] == 21.0
    assert attrs["min_temp"] == 5
    assert attrs["max_temp"] == 30
    assert attrs["target_temp_step"] == 0.5
    assert attrs[ATTR_SUPPORTED_FEATURES] == (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    assert attrs["period"] == "day"
    assert attrs["day_temp"] == 21.0
    assert attrs["night_temp"] == 17.0
    assert attrs["day_start"] == "06:00:00"
    assert attrs["night_start"] == "22:00:00"
    assert attrs["override"] is False
    assert attrs["next_switch"].startswith("2026-01-15T22:00:00")


async def test_hvac_modes_and_turn_on(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Heat, cool and off are stored; turn_on restores the last active mode."""
    await climate_call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: "cool"})
    assert hass.states.get(CLIMATE).state == HVACMode.COOL

    await climate_call(hass, SERVICE_TURN_OFF)
    assert hass.states.get(CLIMATE).state == HVACMode.OFF

    await climate_call(hass, SERVICE_TURN_ON)
    assert hass.states.get(CLIMATE).state == HVACMode.COOL


async def test_set_temperature_changes_active_period(
    hass: HomeAssistant, local_time, entry: MockConfigEntry
) -> None:
    """The thermostat's target edits the temperature of the current period."""
    await climate_call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 22.5})
    assert hass.states.get(DAY_TEMP).state == "22.5"
    assert hass.states.get(NIGHT_TEMP).state == "17.0"
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 22.5

    await tick(hass, local_time, 23)
    await climate_call(
        hass,
        SERVICE_SET_TEMPERATURE,
        **{ATTR_TEMPERATURE: 16, ATTR_HVAC_MODE: "cool"},
    )
    assert hass.states.get(NIGHT_TEMP).state == "16.0"
    assert hass.states.get(DAY_TEMP).state == "22.5"
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.COOL
    assert state.attributes[ATTR_TEMPERATURE] == 16


async def test_set_temperature_out_of_range(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Targets outside 5..30 are rejected."""
    with pytest.raises(ServiceValidationError):
        await climate_call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 35})
    with pytest.raises(ServiceValidationError):
        await set_number(hass, DAY_TEMP, 31)
    assert hass.states.get(DAY_TEMP).state == "21.0"


async def test_config_entities_update_climate(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Changing a configuration entity updates the climate entity right away."""
    await set_number(hass, DAY_TEMP, 22.5)
    await set_number(hass, NIGHT_TEMP, 16)
    await set_time(hass, DAY_START, "07:30:00")
    await set_time(hass, NIGHT_START, "21:15")

    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_TEMPERATURE] == 22.5
    assert attrs["day_temp"] == 22.5
    assert attrs["night_temp"] == 16.0
    assert attrs["day_start"] == "07:30:00"
    assert attrs["night_start"] == "21:15:00"

    # Moving night start before "now" (12:00) switches to night right away.
    await set_time(hass, NIGHT_START, "11:00")
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_TEMPERATURE] == 16.0
    assert attrs[ATTR_PRESET_MODE] == "night"


async def test_set_profile_action(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """heating_profile.set_profile changes several settings at once."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_PROFILE,
        {
            ATTR_ENTITY_ID: CLIMATE,
            "day_temperature": 20.5,
            "night_temperature": 18,
            "day_start": "05:30",
            "night_start": "23:00:00",
        },
        blocking=True,
    )
    assert hass.states.get(DAY_TEMP).state == "20.5"
    assert hass.states.get(NIGHT_TEMP).state == "18.0"
    assert hass.states.get(DAY_START).state == "05:30:00"
    assert hass.states.get(NIGHT_START).state == "23:00:00"

    # Partial update keeps the other values.
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_PROFILE,
        {ATTR_ENTITY_ID: CLIMATE, "night_temperature": 17.5},
        blocking=True,
    )
    assert hass.states.get(NIGHT_TEMP).state == "17.5"
    assert hass.states.get(DAY_TEMP).state == "20.5"

    with pytest.raises(Exception, match="value must be at most 30"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_PROFILE,
            {ATTR_ENTITY_ID: CLIMATE, "day_temperature": 40},
            blocking=True,
        )


@pytest.mark.parametrize(
    ("hour", "minute", "period", "temp"),
    [
        (5, 59, "night", 17.0),
        (6, 0, "day", 21.0),
        (12, 0, "day", 21.0),
        (21, 59, "day", 21.0),
        (22, 0, "night", 17.0),
        (0, 0, "night", 17.0),
    ],
)
async def test_day_night_default_range(
    hass: HomeAssistant,
    local_time,
    entry: MockConfigEntry,
    hour: int,
    minute: int,
    period: str,
    temp: float,
) -> None:
    """Default range 06:00-22:00, boundaries included at start."""
    local_time(hour, minute)
    await async_update_entity(hass, CLIMATE)

    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_TEMPERATURE] == temp
    assert attrs["period"] == period
    assert attrs[ATTR_PRESET_MODE] == period


@pytest.mark.parametrize(
    ("hour", "minute", "period"),
    [
        (19, 59, "night"),
        (20, 0, "day"),
        (23, 59, "day"),
        (0, 0, "day"),
        (3, 59, "day"),
        (4, 0, "night"),
        (12, 0, "night"),
    ],
)
async def test_day_range_crossing_midnight(
    hass: HomeAssistant,
    local_time,
    entry: MockConfigEntry,
    hour: int,
    minute: int,
    period: str,
) -> None:
    """Day from 20:00 to 04:00 (e.g. night shift) wraps around midnight."""
    await set_time(hass, DAY_START, "20:00")
    await set_time(hass, NIGHT_START, "04:00")

    local_time(hour, minute)
    await async_update_entity(hass, CLIMATE)

    attrs = hass.states.get(CLIMATE).attributes
    assert attrs["period"] == period
    assert attrs[ATTR_TEMPERATURE] == (21.0 if period == "day" else 17.0)


def test_is_day_equal_times_is_night() -> None:
    """An empty day range means it is always night."""
    assert not is_day(time(6), time(6), time(6))
    assert not is_day(time(12), time(6), time(6))


async def test_minute_tick_switches_period(
    hass: HomeAssistant, local_time, entry: MockConfigEntry
) -> None:
    """Crossing the night start is picked up by the minute tick."""
    await tick(hass, local_time, 21, 59)
    state = hass.states.get(CLIMATE)
    assert state.attributes["period"] == "day"
    assert state.attributes["next_switch"].startswith("2026-01-15T22:00:00")

    await tick(hass, local_time, 22, 0)
    state = hass.states.get(CLIMATE)
    assert state.attributes["period"] == "night"
    assert state.attributes[ATTR_TEMPERATURE] == 17.0
    assert state.attributes["next_switch"].startswith("2026-01-16T06:00:00")


async def test_preset_overrides_until_next_switch(
    hass: HomeAssistant, local_time, entry: MockConfigEntry
) -> None:
    """Choosing the other preset forces it until the schedule switches."""
    await climate_call(hass, SERVICE_SET_PRESET_MODE, **{ATTR_PRESET_MODE: "night"})
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_PRESET_MODE] == "night"
    assert attrs[ATTR_TEMPERATURE] == 17.0
    assert attrs["override"] is True
    assert attrs["next_switch"].startswith("2026-01-15T22:00:00")

    # Editing the target during the override changes the night temperature.
    await climate_call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 17.5})
    assert hass.states.get(NIGHT_TEMP).state == "17.5"

    await tick(hass, local_time, 21, 59)
    assert hass.states.get(CLIMATE).attributes["override"] is True

    # At 22:00 the schedule is night anyway; the override is gone.
    await tick(hass, local_time, 22, 0)
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs["override"] is False
    assert attrs[ATTR_PRESET_MODE] == "night"

    # Forcing day at night lasts until the next switch (06:00).
    await tick(hass, local_time, 23, 0)
    await climate_call(hass, SERVICE_SET_PRESET_MODE, **{ATTR_PRESET_MODE: "day"})
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs[ATTR_PRESET_MODE] == "day"
    assert attrs[ATTR_TEMPERATURE] == 21.0
    assert attrs["next_switch"].startswith("2026-01-16T06:00:00")

    # Selecting the scheduled period again cancels the override.
    await climate_call(hass, SERVICE_SET_PRESET_MODE, **{ATTR_PRESET_MODE: "night"})
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs["override"] is False
    assert attrs[ATTR_PRESET_MODE] == "night"


async def test_changing_schedule_clears_override(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An override belongs to the old schedule and is dropped on changes."""
    await climate_call(hass, SERVICE_SET_PRESET_MODE, **{ATTR_PRESET_MODE: "night"})
    await set_time(hass, NIGHT_START, "23:00")
    attrs = hass.states.get(CLIMATE).attributes
    assert attrs["override"] is False
    assert attrs[ATTR_PRESET_MODE] == "day"


async def test_persistence_after_reload(
    hass: HomeAssistant, local_time, entry: MockConfigEntry, hass_storage
) -> None:
    """Values, mode and override survive a reload; times stored as ISO."""
    await set_number(hass, DAY_TEMP, 23)
    await set_number(hass, NIGHT_TEMP, 15.5)
    await set_time(hass, DAY_START, "05:45")
    await set_time(hass, NIGHT_START, "23:30")
    await climate_call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: "cool"})
    await climate_call(hass, SERVICE_SET_PRESET_MODE, **{ATTR_PRESET_MODE: "night"})

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert stored["day_temp"] == 23.0
    assert stored["night_temp"] == 15.5
    assert stored["day_start"] == "05:45:00"
    assert stored["night_start"] == "23:30:00"
    assert stored["hvac_mode"] == "cool"
    assert stored["override_period"] == "night"
    assert stored["override_until"].startswith("2026-01-15T23:30:00")

    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.COOL
    assert state.attributes[ATTR_PRESET_MODE] == "night"
    assert state.attributes[ATTR_TEMPERATURE] == 15.5
    assert hass.states.get(DAY_TEMP).state == "23.0"
    assert hass.states.get(DAY_START).state == "05:45:00"
    assert hass.states.get(NIGHT_START).state == "23:30:00"


async def test_delayed_save(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage
) -> None:
    """A delayed save is flushed to storage on its own."""
    await set_number(hass, NIGHT_TEMP, 18.5)
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["night_temp"] == 18.5


async def test_unload_and_remove_deletes_storage(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage
) -> None:
    """Removing the entry deletes its storage file."""
    await set_number(hass, DAY_TEMP, 24)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    key = f"{DOMAIN}.{entry.entry_id}"
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_corrupt_storage_falls_back_to_defaults(
    hass: HomeAssistant, hass_storage
) -> None:
    """Broken stored values do not prevent setup."""
    config_entry = MockConfigEntry(domain=DOMAIN, title="Living room", data={})
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": {
            "day_temp": "warm",
            "day_start": "nope",
            "night_temp": 16,
            "hvac_mode": "dry",
            "override_period": "night",
            "override_until": "garbage",
        },
    }
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(DAY_TEMP).state == "21.0"
    assert hass.states.get(NIGHT_TEMP).state == "16.0"
    assert hass.states.get(DAY_START).state == "06:00:00"
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.HEAT
    assert state.attributes["override"] is False


async def test_old_sensor_is_removed(hass: HomeAssistant) -> None:
    """The target temperature sensor from 0.1.0 is cleaned up."""
    config_entry = MockConfigEntry(domain=DOMAIN, title="Living room", data={})
    config_entry.add_to_hass(hass)
    entity_reg = er.async_get(hass)
    old = entity_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{config_entry.entry_id}_target_temperature",
        config_entry=config_entry,
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert entity_reg.async_get(old.entity_id) is None


async def test_card_is_served(
    hass: HomeAssistant, entry: MockConfigEntry, hass_client
) -> None:
    """The bundled dashboard card is reachable over HTTP."""
    client = await hass_client()
    response = await client.get("/heating_profile/heating-profile-card.js")
    assert response.status == 200
    assert "heating-profile-card" in await response.text()
