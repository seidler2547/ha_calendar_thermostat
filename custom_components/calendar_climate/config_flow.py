"""Config flow for the Calendar Climate integration."""

from collections.abc import Mapping
from typing import Any, cast, override

from homeassistant.components.calendar import DOMAIN as CALENDAR_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.const import CONF_NAME, DEGREE
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaCommonFlowHandler,
    SchemaConfigFlowHandler,
    SchemaFlowError,
    SchemaFlowFormStep,
)
import probatio

from .const import (
    CONF_CALENDAR_ENTITY,
    CONF_CLIMATE_ENTITIES,
    CONF_END_HVAC_MODE,
    CONF_END_TEMPERATURE,
    CONF_EVENT_HVAC_MODE,
    CONF_EVENT_TEMPERATURE,
    CONF_MAX_PREHEAT,
    CONF_PREHEAT_GRADIENT,
    DOMAIN,
    END_PREVIOUS,
    SETPOINT_MODES,
)

HVAC_MODES = [mode.value for mode in HVACMode]

TEMPERATURE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        mode=selector.NumberSelectorMode.BOX, unit_of_measurement=DEGREE, step=0.1
    )
)

OPTIONS_SCHEMA = {
    probatio.Required(CONF_CALENDAR_ENTITY): selector.EntitySelector(
        selector.EntitySelectorConfig(domain=CALENDAR_DOMAIN)
    ),
    probatio.Required(CONF_CLIMATE_ENTITIES): selector.EntitySelector(
        selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN, multiple=True)
    ),
    probatio.Required(
        CONF_EVENT_HVAC_MODE, default=HVACMode.HEAT.value
    ): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=HVAC_MODES,
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key="hvac_mode",
        )
    ),
    probatio.Optional(CONF_EVENT_TEMPERATURE): TEMPERATURE_SELECTOR,
    probatio.Required(
        CONF_END_HVAC_MODE, default=HVACMode.HEAT.value
    ): selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[*HVAC_MODES, END_PREVIOUS],
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key="end_hvac_mode",
        )
    ),
    probatio.Optional(CONF_END_TEMPERATURE): TEMPERATURE_SELECTOR,
    probatio.Required(CONF_PREHEAT_GRADIENT, default=0): selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement=f"{DEGREE}/h",
            step="any",
        )
    ),
    probatio.Required(
        CONF_MAX_PREHEAT, default={"hours": 4}
    ): selector.DurationSelector(selector.DurationSelectorConfig(allow_negative=False)),
}

CONFIG_SCHEMA = {
    probatio.Required(CONF_NAME): selector.TextSelector(),
    **OPTIONS_SCHEMA,
}


async def _validate(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Require a temperature for the modes which have a setpoint."""
    for mode, temperature in (
        (CONF_EVENT_HVAC_MODE, CONF_EVENT_TEMPERATURE),
        (CONF_END_HVAC_MODE, CONF_END_TEMPERATURE),
    ):
        if user_input[mode] in SETPOINT_MODES and temperature not in user_input:
            raise SchemaFlowError("temperature_required")
    return user_input


class ConfigFlowHandler(SchemaConfigFlowHandler, domain=DOMAIN):
    """Handle a config or options flow."""

    config_flow = {
        "user": SchemaFlowFormStep(
            probatio.Schema(CONFIG_SCHEMA), validate_user_input=_validate
        )
    }
    options_flow = {
        "init": SchemaFlowFormStep(
            probatio.Schema(OPTIONS_SCHEMA), validate_user_input=_validate
        )
    }
    options_flow_reloads = True

    @override
    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return config entry title."""
        return cast(str, options[CONF_NAME])
