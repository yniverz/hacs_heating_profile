"""Switches of the climate control: on/off and anti short cycle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingProfileConfigEntry
from .entity import ControlEntity

DESCRIPTION = SwitchEntityDescription(key="control", translation_key="control")
ANTI_SHORT_CYCLE = SwitchEntityDescription(
    key="anti_short_cycle", translation_key="anti_short_cycle"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches if the profile controls an AC."""
    if entry.runtime_data.controller is not None:
        async_add_entities(
            [
                ControlSwitch(entry, DESCRIPTION),
                AntiShortCycleSwitch(entry, ANTI_SHORT_CYCLE),
            ]
        )


class ControlSwitch(ControlEntity, SwitchEntity):
    """On: the profile drives the AC. Turning it on also ends a pause."""

    @property
    def is_on(self) -> bool:
        """Return True if the control is on."""
        return self._controller.state.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the control on."""
        await self._controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the control off (the AC keeps its last setting)."""
        await self._controller.async_set_enabled(False)


class AntiShortCycleSwitch(ControlEntity, SwitchEntity):
    """On: the control runs the compressor in long runs and rests itself."""

    @property
    def is_on(self) -> bool:
        """Return True if the anti short cycle is on."""
        return self._controller.state.anti_short_cycle

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the anti short cycle on."""
        await self._controller.async_set_anti_short_cycle(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn it off: the AC holds the setpoint by itself again."""
        await self._controller.async_set_anti_short_cycle(False)
