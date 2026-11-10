"""Sensor showing what the schedule is doing."""

from typing import Any, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .scheduler import CalendarClimateConfigEntry, ScheduleState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CalendarClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the schedule sensor."""
    async_add_entities([ScheduleSensor(entry)])


class ScheduleSensor(SensorEntity):
    """The state of the schedule: idle, preheat or event."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [state.value for state in ScheduleState]
    _attr_should_poll = False
    _attr_translation_key = "schedule"
    _unrecorded_attributes = frozenset(
        {
            "calendar_available",
            "event_start",
            "event_end",
            "estimated_ready_time",
            "next_event_start",
            "preheat_start",
        }
    )

    def __init__(self, entry: CalendarClimateConfigEntry) -> None:
        """Initialize the sensor."""
        self._scheduler = entry.runtime_data
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the scheduler."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._scheduler.signal, self.async_write_ha_state
            )
        )

    @property
    @override
    def native_value(self) -> str:
        """Return the state of the schedule."""
        return self._scheduler.state

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the times of the schedule."""
        scheduler = self._scheduler
        target, upcoming = scheduler.target, scheduler.upcoming
        return {
            "calendar_available": scheduler.calendar_available,
            "event_start": target.start if target else None,
            "event_end": target.end if target else None,
            "estimated_ready_time": scheduler.estimated_ready_time,
            "next_event_start": upcoming.start if upcoming else None,
            "preheat_start": scheduler.preheat_start,
        }
