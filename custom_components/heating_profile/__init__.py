"""The Heating Profile integration.

A virtual thermostat-like device that controls nothing. It stores day/night
temperatures, start times and a heat/cool/off mode that automations can read.
"""

from __future__ import annotations

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

from .const import CARD_FILENAME, CARD_URL, DOMAIN, STORAGE_VERSION
from .profile import HeatingProfileData, storage_key

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.NUMBER, Platform.TIME]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HeatingProfileConfigEntry = ConfigEntry[HeatingProfileData]


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
    entry.runtime_data = data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> bool:
    """Unload a config entry."""
    # Flush pending delayed saves so a reload reads the latest values.
    await entry.runtime_data.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: HeatingProfileConfigEntry
) -> None:
    """Delete the stored settings when the entry is removed."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
