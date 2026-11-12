"""Buttons: start day/night early (one-off), end a pause of the control."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HeatingProfileConfigEntry
from .const import PERIOD_DAY, PERIOD_NIGHT
from .entity import ControlEntity, HeatingProfileEntity

# The key is the period the button starts.
PERIOD_DESCRIPTIONS = (
    ButtonEntityDescription(key=PERIOD_DAY, translation_key="start_day"),
    ButtonEntityDescription(key=PERIOD_NIGHT, translation_key="start_night"),
)
END_PAUSE = ButtonEntityDescription(key="end_pause", translation_key="end_pause")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HeatingProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the period buttons (and End pause if the profile controls an AC)."""
    entities: list[ButtonEntity] = [
        StartPeriodButton(entry, d) for d in PERIOD_DESCRIPTIONS
    ]
    if entry.runtime_data.controller is not None:
        entities.append(EndPauseButton(entry, END_PAUSE))
    async_add_entities(entities)


class StartPeriodButton(HeatingProfileEntity, ButtonEntity):
    """Start day (night) now, until the next scheduled switch.

    Pressing it again, or while that period is active anyway, changes
    nothing; it cancels an override of the other period.
    """

    def __init__(
        self, entry: HeatingProfileConfigEntry, description: ButtonEntityDescription
    ) -> None:
        """Initialize; unique IDs start_day / start_night."""
        super().__init__(entry, description)
        self._attr_unique_id = f"{entry.entry_id}_start_{description.key}"

    async def async_press(self) -> None:
        """Start the period now."""
        period = self.entity_description.key
        now = dt_util.utcnow()
        if self._data.period(now) != period:
            self._data.async_set_period(period, now)


class EndPauseButton(ControlEntity, ButtonEntity):
    """Resume the control right away."""

    async def async_press(self) -> None:
        """End the pause."""
        await self._controller.async_end_pause()
