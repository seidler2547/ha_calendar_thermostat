"""Fixtures for the Calendar Climate tests."""

from datetime import datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.calendar_climate.const import (
    CONF_CALENDAR_ENTITY,
    CONF_CLIMATE_ENTITIES,
    CONF_END_HVAC_MODE,
    CONF_END_TEMPERATURE,
    CONF_EVENT_HVAC_MODE,
    CONF_EVENT_TEMPERATURE,
    CONF_MAX_PREHEAT,
    CONF_PREHEAT_GRADIENT,
    DOMAIN,
)

CALENDAR = "calendar.room"
HALL = "climate.hall"
STAGE = "climate.stage"
SENSOR = "sensor.hall"


def at(hour: int, minute: int = 0) -> datetime:
    """Return a time of the test day."""
    return datetime(2026, 1, 5, hour, minute, tzinfo=dt_util.get_default_time_zone())


def meeting(
    start: tuple[int, int] = (10, 0), end: tuple[int, int] = (12, 0)
) -> CalendarEvent:
    """Return an event of the test day."""
    return CalendarEvent(start=at(*start), end=at(*end), summary="Meeting")


class MockCalendar(CalendarEntity):
    """A calendar whose events are set by the test."""

    _attr_should_poll = False

    def __init__(self) -> None:
        """Initialize the calendar."""
        self.entity_id = CALENDAR
        self.events: list[CalendarEvent] = []

    @property
    def event(self) -> CalendarEvent | None:
        """Return no current event: the state stays off."""
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the events of the time span."""
        return [
            event
            for event in self.events
            if event.start_datetime_local < end_date
            and event.end_datetime_local > start_date
        ]

    def set_available(self, available: bool) -> None:
        """Simulate a calendar which cannot be synchronised."""
        self._attr_available = available
        self.async_write_ha_state()


class FakeClimates:
    """Climate entities which follow the service calls and record them."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Register the climate services."""
        self.hass = hass
        self.calls: list[tuple[str, Any]] = []
        self.errors: dict[str, Exception] = {}
        hass.services.async_register("climate", "set_hvac_mode", self._set_hvac_mode)
        hass.services.async_register(
            "climate", "set_temperature", self._set_temperature
        )

    def set(
        self,
        entity_id: str,
        mode: str = "heat",
        current: float | None = 15,
        target: float | None = 17,
    ) -> None:
        """Set the state of a climate entity."""
        self.hass.states.async_set(
            entity_id, mode, {"current_temperature": current, "temperature": target}
        )

    def pop_calls(self) -> list[tuple[str, Any]]:
        """Return the calls since the last time, sorted by entity."""
        calls, self.calls = self.calls, []
        return sorted(calls, key=lambda call: call[0])

    def _record(self, call: ServiceCall, value: Any) -> str:
        entity_id = call.data["entity_id"]
        if error := self.errors.get(entity_id):
            raise error
        self.calls.append((entity_id, value))
        return entity_id

    async def _set_hvac_mode(self, call: ServiceCall) -> None:
        entity_id = self._record(call, call.data["hvac_mode"])
        self.hass.states.async_set(
            entity_id,
            call.data["hvac_mode"],
            self.hass.states.get(entity_id).attributes,
        )

    async def _set_temperature(self, call: ServiceCall) -> None:
        entity_id = self._record(call, call.data["temperature"])
        state = self.hass.states.get(entity_id)
        self.hass.states.async_set(
            entity_id,
            state.state,
            {**state.attributes, "temperature": call.data["temperature"]},
        )


@pytest.fixture(autouse=True)
def start_time(
    freezer: FrozenDateTimeFactory, hass_storage: dict[str, Any], hass: HomeAssistant
) -> None:
    """Start every test at 6:00 with the stored schedules kept in memory."""
    freezer.move_to(at(6))


@pytest.fixture
async def calendar(hass: HomeAssistant) -> MockCalendar:
    """Return a calendar entity served by the calendar integration."""
    assert await async_setup_component(hass, "calendar", {})
    mock_calendar = MockCalendar()
    await hass.data["calendar"].async_add_entities([mock_calendar])
    return mock_calendar


@pytest.fixture
async def climates(hass: HomeAssistant) -> FakeClimates:
    """Return two climate entities at 15° set to 17°: an event at 21° needs 2 hours at 3°/h."""
    assert await async_setup_component(hass, "climate", {})
    fake_climates = FakeClimates(hass)
    fake_climates.set(HALL)
    fake_climates.set(STAGE, current=18)
    return fake_climates


def options(**changes: Any) -> dict[str, Any]:
    """Return the options of a schedule: heat at 21° during the events, at 17° after."""
    return {
        CONF_CALENDAR_ENTITY: CALENDAR,
        CONF_CLIMATE_ENTITIES: [HALL, STAGE],
        CONF_EVENT_HVAC_MODE: "heat",
        CONF_EVENT_TEMPERATURE: 21,
        CONF_END_HVAC_MODE: "heat",
        CONF_END_TEMPERATURE: 17,
        CONF_PREHEAT_GRADIENT: 3,
        CONF_MAX_PREHEAT: {"hours": 4},
    } | changes


async def setup(hass: HomeAssistant, **changes: Any) -> MockConfigEntry:
    """Set up a schedule and wait for its first read of the calendar."""
    entry = MockConfigEntry(domain=DOMAIN, title="Hall", options=options(**changes))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def move(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    entry: MockConfigEntry,
    when: datetime,
) -> None:
    """Move the clock and read the calendar as the timers do."""
    freezer.move_to(when)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


EVENT_CALLS = [(HALL, "heat"), (HALL, 21), (STAGE, "heat"), (STAGE, 21)]
END_CALLS = [(HALL, "heat"), (HALL, 17), (STAGE, "heat"), (STAGE, 17)]
QUARTER = timedelta(minutes=15)
