"""Shared fixtures."""

from __future__ import annotations

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_profile.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def short_confirm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mocked AC services don't change the AC's state: don't wait."""
    monkeypatch.setattr(
        "custom_components.heating_profile.controller.CONFIRM_TIMEOUT_SECONDS", 0
    )


@pytest.fixture
def local_time(freezer: FrozenDateTimeFactory):
    """Return a helper that freezes the clock at a local HH:MM:SS today."""

    def _set(hour: int, minute: int = 0, second: int = 0) -> None:
        freezer.move_to(
            datetime(
                2026,
                1,
                15,
                hour,
                minute,
                second,
                tzinfo=dt_util.get_default_time_zone(),
            )
        )

    return _set


@pytest.fixture
async def entry(hass: HomeAssistant, local_time) -> MockConfigEntry:
    """Create and set up a heating profile named 'Living room' at 12:00."""
    local_time(12)
    config_entry = MockConfigEntry(domain=DOMAIN, title="Living room", data={})
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
