"""The Heating Profile integration.

A virtual thermostat-like device that stores day/night temperature ranges,
start times and a heat/cool/heat_cool/off mode. Optionally (options flow) it
drives an air conditioner from a room sensor to keep that range.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CARD_FILENAME,
    CARD_URL,
    CONTROL_STORAGE_VERSION,
    DOMAIN,
    STORAGE_VERSION,
)
from .controller import ClimateController, control_configured, control_storage_key
from .profile import HeatingProfileData, storage_key

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Entities that only exist while the profile controls an AC (key -> platform).
_CONTROL_ENTITIES: dict[str, Platform] = {
    "control": Platform.SWITCH,
    "end_pause": Platform.BUTTON,
    "offset_heat": Platform.NUMBER,
    "offset_cool": Platform.NUMBER,
    **{
        key: Platform.SENSOR
        for key in (
            "control_status",
            "control_reason",
            "control_state",
            "room_average",
            "room_trend",
            "forecast_min",
            "forecast_max",
            "forecast_radiation",
            "ac_setpoint",
            "waiting_until",
            "paused_until",
        )
    },
}
CONTROL_ENTITY_KEYS = tuple(_CONTROL_ENTITIES)


def platform_for_key(key: str) -> Platform:
    """Platform of a control entity."""
    return _CONTROL_ENTITIES[key]


@dataclass
class HeatingProfileRuntime:
    """Runtime data of an entry: the profile and its optional control."""

    profile: HeatingProfileData
    controller: ClimateController | None


type HeatingProfileConfigEntry = ConfigEntry[HeatingProfileRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the dashboard card and load it in the frontend."""
    path = Path(__file__).parent / "frontend" / CARD_FILENAME
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(path), cache_headers=False)]
    )
    if "frontend" in hass.config.components:
        version = (await async_get_integration(hass, DOMAIN)).version
        # The version query makes browsers fetch the card again after updates.
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> bool:
    """Set up a heating profile from a config entry."""
    # Version 0.1.0 had a target temperature sensor; the climate entity
    # replaces it.
    ent_reg = er.async_get(hass)
    if old := ent_reg.async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{entry.entry_id}_target_temperature"
    ):
        ent_reg.async_remove(old)

    data = HeatingProfileData(hass, entry.entry_id)
    await data.async_load()
    controller = None
    if control_configured(dict(entry.options)):
        controller = ClimateController(hass, entry.entry_id, data, dict(entry.options))
    else:
        # The control was removed in the options: drop its entities.
        for key in CONTROL_ENTITY_KEYS:
            if entity_id := ent_reg.async_get_entity_id(
                platform_for_key(key), DOMAIN, f"{entry.entry_id}_{key}"
            ):
                ent_reg.async_remove(entity_id)
    entry.runtime_data = HeatingProfileRuntime(data, controller)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if controller is not None:
        await controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


# Old default -> new default of the forecast margins (0.7.0 -> 0.7.1): they
# now count from the opposite limit of the range. Values set by hand stay.
_MARGIN_MIGRATION = {
    "cool_margin": (2.0, 1.0),
    "exit_warmth_margin": (3.0, 2.0),
    "exit_cool_margin": (3.0, 2.0),
}


async def async_migrate_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> bool:
    """Migrate the options of older entries."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        options = dict(entry.options)
        for key, (old, new) in _MARGIN_MIGRATION.items():
            if options.get(key) == old:
                options[key] = new
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> None:
    """Apply changed options by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> bool:
    """Unload a config entry."""
    # Flush pending delayed saves so a reload reads the latest values.
    runtime = entry.runtime_data
    if runtime.controller is not None:
        await runtime.controller.async_stop()
    await runtime.profile.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> None:
    """Delete the stored settings when the entry is removed."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
    await Store(
        hass, CONTROL_STORAGE_VERSION, control_storage_key(entry.entry_id)
    ).async_remove()
