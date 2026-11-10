"""Test the Calendar Climate config flow."""

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.calendar_climate.const import (
    CONF_END_HVAC_MODE,
    CONF_END_TEMPERATURE,
    CONF_EVENT_TEMPERATURE,
    CONF_PREHEAT_GRADIENT,
    DOMAIN,
    END_PREVIOUS,
)

from .conftest import (
    EVENT_CALLS,
    HALL,
    SENSOR,
    STAGE,
    FakeClimates,
    MockCalendar,
    at,
    meeting,
    move,
    options,
    setup,
)


async def test_user_flow(
    hass: HomeAssistant, calendar: MockCalendar, climates: FakeClimates
) -> None:
    """The flow creates a schedule with the entered options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    user_input = {CONF_NAME: "Hall", **options()}
    del user_input[CONF_EVENT_TEMPERATURE]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "temperature_required"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Hall", **options()}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hall"
    assert result["options"] == {CONF_NAME: "Hall", **options()}
    assert hass.states.get(SENSOR).state == "idle"


async def test_options_flow(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """Changed options are applied at once, without forgetting the running event."""
    calendar.events = [meeting()]
    entry = await setup(hass)
    await move(hass, freezer, entry, at(8))
    assert climates.pop_calls() == EVENT_CALLS

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    user_input = options(**{CONF_END_HVAC_MODE: "off", CONF_PREHEAT_GRADIENT: 1})
    del user_input[CONF_END_TEMPERATURE]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_END_TEMPERATURE not in entry.options

    assert hass.states.get(SENSOR).state == "preheat"
    await move(hass, freezer, entry, at(12))
    assert climates.pop_calls() == [(HALL, "off"), (STAGE, "off")]


async def test_previous_state_needs_no_temperature(
    hass: HomeAssistant, calendar: MockCalendar, climates: FakeClimates
) -> None:
    """Restoring the previous state does not ask for a temperature."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    user_input = {CONF_NAME: "Hall", **options(**{CONF_END_HVAC_MODE: END_PREVIOUS})}
    del user_input[CONF_END_TEMPERATURE]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
