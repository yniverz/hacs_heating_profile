"""Entity, logic and persistence tests."""

from datetime import time, timedelta

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
from homeassistant.const import ATTR_ENTITY_ID, ATTR_ICON, ATTR_UNIT_OF_MEASUREMENT
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

from custom_components.heating_profile import is_day
from custom_components.heating_profile.const import DOMAIN

DAY_TEMP = "number.living_room_day_temperature"
NIGHT_TEMP = "number.living_room_night_temperature"
DAY_START = "time.living_room_day_starts"
NIGHT_START = "time.living_room_night_starts"
TARGET = "sensor.living_room_target_temperature"


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


async def test_device_and_entities(
    hass: HomeAssistant, local_time, entry: MockConfigEntry
) -> None:
    """One device with five entities, defaults, units and icons."""
    device_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_reg, entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.name == "Living room"
    assert device.manufacturer == "Custom"
    assert device.model == "Heating profile"

    entity_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(entity_reg, entry.entry_id)
    assert {e.unique_id for e in entities} == {
        f"{entry.entry_id}_{key}"
        for key in (
            "day_temp",
            "night_temp",
            "day_start",
            "night_start",
            "target_temperature",
        )
    }
    assert all(e.device_id == device.id for e in entities)

    expected = {
        DAY_TEMP: ("21.0", "mdi:weather-sunny"),
        NIGHT_TEMP: ("17.0", "mdi:weather-night"),
        DAY_START: ("06:00:00", "mdi:weather-sunset-up"),
        NIGHT_START: ("22:00:00", "mdi:weather-sunset-down"),
    }
    for entity_id, (value, icon) in expected.items():
        state = hass.states.get(entity_id)
        assert state.state == value, entity_id
        assert state.attributes[ATTR_ICON] == icon

    day = hass.states.get(DAY_TEMP)
    assert day.attributes["min"] == 5
    assert day.attributes["max"] == 30
    assert day.attributes["step"] == 0.5
    assert day.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    assert day.attributes["device_class"] == "temperature"

    target = hass.states.get(TARGET)
    assert target.attributes[ATTR_ICON] == "mdi:thermostat"
    assert target.attributes["device_class"] == "temperature"
    assert target.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"


async def test_set_values_updates_sensor_immediately(
    hass: HomeAssistant, local_time, entry: MockConfigEntry
) -> None:
    """Changing a setting updates the target sensor without waiting."""
    local_time(12)
    await set_number(hass, DAY_TEMP, 22.5)
    await set_number(hass, NIGHT_TEMP, 16)
    await set_time(hass, DAY_START, "07:30:00")
    await set_time(hass, NIGHT_START, "21:15")

    assert hass.states.get(DAY_TEMP).state == "22.5"
    assert hass.states.get(NIGHT_TEMP).state == "16.0"
    assert hass.states.get(DAY_START).state == "07:30:00"
    assert hass.states.get(NIGHT_START).state == "21:15:00"

    target = hass.states.get(TARGET)
    assert target.state == "22.5"
    assert target.attributes == target.attributes | {
        "period": "day",
        "day_temp": 22.5,
        "night_temp": 16.0,
        "day_start": "07:30:00",
        "night_start": "21:15:00",
    }

    # Moving night start before "now" switches to night right away.
    await set_time(hass, NIGHT_START, "11:00")
    target = hass.states.get(TARGET)
    assert target.state == "16.0"
    assert target.attributes["period"] == "night"


async def test_number_rejects_out_of_range(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Values outside 5..30 are refused by the number platform."""
    with pytest.raises(ServiceValidationError):
        await set_number(hass, DAY_TEMP, 31)
    assert hass.states.get(DAY_TEMP).state == "21.0"


@pytest.mark.parametrize(
    ("hour", "minute", "period", "temp"),
    [
        (5, 59, "night", "17.0"),
        (6, 0, "day", "21.0"),
        (12, 0, "day", "21.0"),
        (21, 59, "day", "21.0"),
        (22, 0, "night", "17.0"),
        (0, 0, "night", "17.0"),
    ],
)
async def test_day_night_default_range(
    hass: HomeAssistant,
    local_time,
    entry: MockConfigEntry,
    hour: int,
    minute: int,
    period: str,
    temp: str,
) -> None:
    """Default range 06:00-22:00, boundaries included at start."""
    local_time(hour, minute)
    await async_update_entity(hass, TARGET)

    state = hass.states.get(TARGET)
    assert state.state == temp
    assert state.attributes["period"] == period


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
    await async_update_entity(hass, TARGET)

    state = hass.states.get(TARGET)
    assert state.attributes["period"] == period
    assert state.state == ("21.0" if period == "day" else "17.0")


def test_is_day_equal_times_is_night() -> None:
    """An empty day range means it is always night."""
    assert not is_day(time(6), time(6), time(6))
    assert not is_day(time(12), time(6), time(6))


async def test_sensor_updates_every_minute(
    hass: HomeAssistant, local_time, entry: MockConfigEntry
) -> None:
    """The minute tick picks up the switch to night without other changes."""
    local_time(21, 59, 0)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get(TARGET)
    assert state.attributes["period"] == "day"
    assert state.state == "21.0"

    local_time(22, 0, 0)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get(TARGET)
    assert state.attributes["period"] == "night"
    assert state.state == "17.0"


async def test_persistence_after_reload(
    hass: HomeAssistant, local_time, entry: MockConfigEntry, hass_storage
) -> None:
    """Values survive a reload and are written as ISO strings."""
    local_time(12)
    await set_number(hass, DAY_TEMP, 23)
    await set_number(hass, NIGHT_TEMP, 15.5)
    await set_time(hass, DAY_START, "05:45")
    await set_time(hass, NIGHT_START, "23:30")

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert stored == {
        "day_temp": 23.0,
        "night_temp": 15.5,
        "day_start": "05:45:00",
        "night_start": "23:30:00",
    }

    assert hass.states.get(DAY_TEMP).state == "23.0"
    assert hass.states.get(NIGHT_TEMP).state == "15.5"
    assert hass.states.get(DAY_START).state == "05:45:00"
    assert hass.states.get(NIGHT_START).state == "23:30:00"
    assert hass.states.get(TARGET).state == "23.0"


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
        "data": {"day_temp": "warm", "day_start": "nope", "night_temp": 16},
    }
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(DAY_TEMP).state == "21.0"
    assert hass.states.get(NIGHT_TEMP).state == "16.0"
    assert hass.states.get(DAY_START).state == "06:00:00"
