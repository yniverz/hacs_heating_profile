"""Config flow tests."""

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.heating_profile.const import DEFAULT_NAME, DOMAIN


async def test_user_flow_default_name(hass: HomeAssistant) -> None:
    """The form defaults to 'Heating profile' and creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    schema_defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert schema_defaults == {CONF_NAME: DEFAULT_NAME}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: DEFAULT_NAME}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == DEFAULT_NAME
    assert result["data"] == {}


async def test_multiple_entries_allowed(hass: HomeAssistant) -> None:
    """One entry per room is possible."""
    for name in ("Kitchen", "Bedroom"):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NAME: name}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert sorted(e.title for e in entries) == ["Bedroom", "Kitchen"]
    assert hass.states.get("sensor.kitchen_target_temperature") is not None
    assert hass.states.get("sensor.bedroom_target_temperature") is not None
