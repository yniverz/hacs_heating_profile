"""Button that ends a pause after a manual change on the AC."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HeatingProfileConfigEntry
from .entity import ControlEntity

DESCRIPTION = ButtonEntityDescription(key="end_pause", translation_key="end_pause")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button if the profile controls an AC."""
    if entry.runtime_data.controller is not None:
        async_add_entities([EndPauseButton(entry, DESCRIPTION)])


class EndPauseButton(ControlEntity, ButtonEntity):
    """Resume the control right away."""

    async def async_press(self) -> None:
        """End the pause."""
        await self._controller.async_end_pause()
