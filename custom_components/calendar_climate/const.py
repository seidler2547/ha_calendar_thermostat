"""Constants for the Calendar Climate integration."""

from datetime import timedelta

from homeassistant.components.climate import HVACMode

DOMAIN = "calendar_climate"

CONF_CALENDAR_ENTITY = "calendar_entity_id"
CONF_CLIMATE_ENTITIES = "climate_entity_ids"
CONF_EVENT_HVAC_MODE = "event_hvac_mode"
CONF_EVENT_TEMPERATURE = "event_temperature"
CONF_END_HVAC_MODE = "end_hvac_mode"
CONF_END_TEMPERATURE = "end_temperature"
CONF_PREHEAT_GRADIENT = "preheat_gradient"
CONF_MAX_PREHEAT = "max_preheat"

END_PREVIOUS = "previous"

# The calendar is read at this pace. A removed event is released when it is
# still missing one read later.
REFRESH_INTERVAL = timedelta(minutes=15)

PREHEAT_MODES = frozenset({HVACMode.HEAT, HVACMode.HEAT_COOL, HVACMode.AUTO})
SETPOINT_MODES = PREHEAT_MODES | {HVACMode.COOL}
