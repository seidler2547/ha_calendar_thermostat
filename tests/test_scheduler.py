"""Test the schedule followed by the Calendar Climate integration."""

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.calendar import CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant.common import async_fire_time_changed

from custom_components.calendar_climate.const import (
    CONF_CLIMATE_ENTITIES,
    CONF_END_HVAC_MODE,
    CONF_EVENT_HVAC_MODE,
    CONF_MAX_PREHEAT,
    CONF_PREHEAT_GRADIENT,
    END_PREVIOUS,
)
from custom_components.calendar_climate.scheduler import Block, merge_events

from .conftest import (
    CALENDAR,
    END_CALLS,
    EVENT_CALLS,
    HALL,
    SENSOR,
    STAGE,
    FakeClimates,
    MockCalendar,
    at,
    meeting,
    move,
    setup,
)


def test_merge_events() -> None:
    """Overlapping and contiguous events are one block, empty events are ignored."""

    def hour(value: int) -> datetime:
        return datetime(2026, 1, 5) + timedelta(hours=value)

    assert merge_events(
        [
            (hour(14), hour(15)),
            (hour(8), hour(10)),
            (hour(9), hour(11)),
            (hour(11), hour(12)),
            (hour(13), hour(13)),
        ]
    ) == [Block(hour(8), hour(12)), Block(hour(14), hour(15))]
    assert Block(hour(8), hour(10)).overlaps(Block(hour(9), hour(11)))
    assert not Block(hour(8), hour(10)).overlaps(Block(hour(10), hour(11)))


async def test_preheat_event_and_end(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """The coldest room (15°) starts the preheat 2 hours before the event and the end settings follow it."""
    calendar.events = [meeting()]
    entry = await setup(hass)

    state = hass.states.get(SENSOR)
    assert state.state == "idle"
    assert state.attributes["calendar_available"] is True
    assert state.attributes["next_event_start"] == at(10)
    assert state.attributes["preheat_start"] == at(8)
    assert climates.pop_calls() == []

    await move(hass, freezer, entry, at(7, 59))
    assert hass.states.get(SENSOR).state == "idle"

    await move(hass, freezer, entry, at(8))
    state = hass.states.get(SENSOR)
    assert state.state == "preheat"
    assert state.attributes["event_start"] == at(10)
    assert state.attributes["event_end"] == at(12)
    assert state.attributes["estimated_ready_time"] == at(10)
    assert climates.pop_calls() == EVENT_CALLS

    # The rooms heat faster than predicted: the preheat is neither released nor sent again
    freezer.move_to(at(8, 30))
    climates.set(HALL, current=20.9, target=21)
    climates.set(STAGE, current=21, target=21)
    await hass.async_block_till_done()
    await move(hass, freezer, entry, at(8, 45))
    assert hass.states.get(SENSOR).state == "preheat"

    await move(hass, freezer, entry, at(10))
    assert hass.states.get(SENSOR).state == "event"
    assert climates.pop_calls() == []

    await move(hass, freezer, entry, at(12))
    assert hass.states.get(SENSOR).state == "idle"
    assert climates.pop_calls() == END_CALLS


async def test_preheat_starts_earlier_when_a_room_cools(
    hass: HomeAssistant, calendar: MockCalendar, climates: FakeClimates
) -> None:
    """A temperature change plans the preheat again without reading the calendar."""
    calendar.events = [meeting()]
    await setup(hass)

    with patch.object(MockCalendar, "async_get_events") as get_events:
        climates.set(STAGE, current=12)
        await hass.async_block_till_done()
        assert hass.states.get(SENSOR).attributes["preheat_start"] == at(7)

        climates.set(STAGE, current=9)
        await hass.async_block_till_done()
        get_events.assert_not_called()

    assert hass.states.get(SENSOR).state == "preheat"
    assert climates.pop_calls() == EVENT_CALLS


async def test_event_appearing_too_late(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """An event known 30 minutes before its start is preheated at once and the room is late."""
    entry = await setup(hass)
    await move(hass, freezer, entry, at(9))
    assert hass.states.get(SENSOR).state == "idle"

    calendar.events = [meeting()]
    await move(hass, freezer, entry, at(9, 30))
    state = hass.states.get(SENSOR)
    assert state.state == "preheat"
    assert state.attributes["estimated_ready_time"] == at(11, 30)
    assert climates.pop_calls() == EVENT_CALLS


async def test_preheat_is_capped(
    hass: HomeAssistant, calendar: MockCalendar, climates: FakeClimates
) -> None:
    """A very cold room does not start the preheat before the maximum preheat duration."""
    climates.set(HALL, current=5)
    calendar.events = [meeting(start=(10, 15), end=(11, 0))]
    await setup(hass)

    state = hass.states.get(SENSOR)
    assert state.state == "idle"
    assert state.attributes["preheat_start"] == at(6, 15)


@pytest.mark.parametrize(
    "changes",
    [
        {CONF_PREHEAT_GRADIENT: 0},
        {CONF_MAX_PREHEAT: {"hours": 0}},
        {CONF_EVENT_HVAC_MODE: "cool"},
    ],
)
async def test_no_preheat(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
    changes: dict[str, Any],
) -> None:
    """Without gradient or duration, or when not heating, the settings change when the event starts."""
    calendar.events = [meeting()]
    entry = await setup(hass, **changes)

    await move(hass, freezer, entry, at(9, 59))
    assert hass.states.get(SENSOR).attributes["preheat_start"] == at(10)
    assert climates.pop_calls() == []

    await move(hass, freezer, entry, at(10))
    assert hass.states.get(SENSOR).state == "event"
    assert len(climates.pop_calls()) == 4


async def test_no_preheat_without_room_temperature(
    hass: HomeAssistant, calendar: MockCalendar, climates: FakeClimates
) -> None:
    """Entities which do not report their room temperature are not preheated."""
    climates.set(HALL, current=None)
    climates.set(STAGE, current=None)
    calendar.events = [meeting(start=(7, 0), end=(8, 0))]
    await setup(hass)

    assert hass.states.get(SENSOR).attributes["preheat_start"] == at(7)


async def test_removed_event_is_released_after_one_read(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """A removed event keeps heating until it is still missing 15 minutes later."""
    calendar.events = [meeting()]
    entry = await setup(hass)
    await move(hass, freezer, entry, at(8))
    assert climates.pop_calls() == EVENT_CALLS

    calendar.events = []
    await move(hass, freezer, entry, at(8, 5))
    await move(hass, freezer, entry, at(8, 19))
    assert hass.states.get(SENSOR).state == "preheat"
    assert entry.runtime_data.missing_since == at(8, 5)
    assert climates.pop_calls() == []

    await move(hass, freezer, entry, at(8, 20))
    assert hass.states.get(SENSOR).state == "idle"
    assert entry.runtime_data.missing_since is None
    assert climates.pop_calls() == END_CALLS


async def test_event_missing_from_one_read_only(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """An event which comes back at the next read never stops the heating."""
    calendar.events = [meeting()]
    entry = await setup(hass)
    await move(hass, freezer, entry, at(8))
    climates.pop_calls()

    calendar.events = []
    await move(hass, freezer, entry, at(8, 5))
    assert entry.runtime_data.missing_since is not None

    calendar.events = [meeting()]
    await move(hass, freezer, entry, at(8, 20))
    assert hass.states.get(SENSOR).state == "preheat"
    assert entry.runtime_data.missing_since is None
    assert climates.pop_calls() == []


async def test_unavailable_calendar_keeps_the_known_events(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """A calendar which cannot be read is not a removed event: the known event runs until its known end."""
    calendar.events = [meeting()]
    entry = await setup(hass)

    calendar.set_available(False)
    await hass.async_block_till_done()
    await move(hass, freezer, entry, at(8))
    state = hass.states.get(SENSOR)
    assert state.state == "preheat"
    assert state.attributes["calendar_available"] is False

    await move(hass, freezer, entry, at(11))
    assert hass.states.get(SENSOR).state == "event"
    assert entry.runtime_data.missing_since is None
    assert climates.pop_calls() == EVENT_CALLS

    await move(hass, freezer, entry, at(12))
    assert hass.states.get(SENSOR).state == "idle"
    assert climates.pop_calls() == END_CALLS


async def test_failing_calendar_read_keeps_the_known_events(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """An error of the calendar integration never breaks the schedule."""
    calendar.events = [meeting()]
    entry = await setup(hass)

    with patch.object(
        MockCalendar, "async_get_events", side_effect=RuntimeError("boom")
    ):
        await move(hass, freezer, entry, at(8))
    state = hass.states.get(SENSOR)
    assert state.state == "preheat"
    assert state.attributes["calendar_available"] is False


async def test_moved_event(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """An event moved onto its previous time span is followed. Moved further away, it is released then preheated again."""
    calendar.events = [meeting()]
    entry = await setup(hass)
    await move(hass, freezer, entry, at(8))
    assert climates.pop_calls() == EVENT_CALLS

    calendar.events = [meeting(start=(11, 0), end=(13, 0))]
    await move(hass, freezer, entry, at(8, 30))
    state = hass.states.get(SENSOR)
    assert state.state == "preheat"
    assert state.attributes["event_start"] == at(11)
    assert entry.runtime_data.missing_since is None

    climates.set(HALL, current=15)
    calendar.events = [meeting(start=(13, 30), end=(14, 0))]
    await move(hass, freezer, entry, at(9))
    assert climates.pop_calls() == []
    await move(hass, freezer, entry, at(9, 15))
    state = hass.states.get(SENSOR)
    assert state.state == "idle"
    assert state.attributes["preheat_start"] == at(11, 30)
    assert climates.pop_calls() == END_CALLS

    await move(hass, freezer, entry, at(11, 30))
    assert hass.states.get(SENSOR).state == "preheat"
    assert climates.pop_calls() == EVENT_CALLS


async def test_consecutive_events_are_one_block(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """The end settings are not applied between two contiguous events."""
    calendar.events = [
        meeting(start=(7, 0), end=(8, 0)),
        meeting(start=(8, 0), end=(9, 0)),
    ]
    entry = await setup(hass)
    assert hass.states.get(SENSOR).state == "preheat"
    assert climates.pop_calls() == EVENT_CALLS

    await move(hass, freezer, entry, at(8))
    state = hass.states.get(SENSOR)
    assert state.state == "event"
    assert state.attributes["event_end"] == at(9)
    assert climates.pop_calls() == []

    await move(hass, freezer, entry, at(9))
    assert climates.pop_calls() == END_CALLS


async def test_all_day_event(
    hass: HomeAssistant, calendar: MockCalendar, climates: FakeClimates
) -> None:
    """An all-day event runs from midnight to midnight."""
    calendar.events = [
        CalendarEvent(start=date(2026, 1, 5), end=date(2026, 1, 6), summary="Open day")
    ]
    await setup(hass)

    state = hass.states.get(SENSOR)
    assert state.state == "event"
    assert state.attributes["event_start"] == at(0)
    assert state.attributes["event_end"] == at(0) + timedelta(days=1)


async def test_return_to_previous_state(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """With the previous option each entity gets back the mode and temperature it had before the event."""
    climates.set(HALL, mode="heat", target=19)
    climates.set(STAGE, mode="off", current=18, target=16)
    calendar.events = [meeting()]
    entry = await setup(hass, **{CONF_END_HVAC_MODE: END_PREVIOUS})

    await move(hass, freezer, entry, at(8))
    assert climates.pop_calls() == EVENT_CALLS

    await move(hass, freezer, entry, at(12))
    assert climates.pop_calls() == [(HALL, "heat"), (HALL, 19), (STAGE, "off")]


async def test_manual_change_is_kept_until_the_event_ends(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """The settings are only sent on transitions: a manual change wins until the next one."""
    calendar.events = [meeting()]
    entry = await setup(hass)
    await move(hass, freezer, entry, at(8))
    climates.pop_calls()

    climates.set(HALL, mode="off", current=16, target=21)
    await hass.async_block_till_done()
    await move(hass, freezer, entry, at(10))
    await move(hass, freezer, entry, at(11))
    assert hass.states.get(HALL).state == "off"
    assert climates.pop_calls() == []

    await move(hass, freezer, entry, at(12))
    assert climates.pop_calls() == END_CALLS


async def test_unavailable_entity_catches_up(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """An entity which is unavailable at a transition gets the settings when it is back."""
    calendar.events = [meeting()]
    entry = await setup(hass)

    climates.set(STAGE, mode="unavailable")
    await move(hass, freezer, entry, at(8))
    assert climates.pop_calls() == [(HALL, "heat"), (HALL, 21)]

    freezer.move_to(at(8, 10))
    climates.set(STAGE, current=18)
    await hass.async_block_till_done()
    assert climates.pop_calls() == [(STAGE, "heat"), (STAGE, 21)]

    # Later changes of the entity are not overwritten
    climates.set(STAGE, mode="off")
    await hass.async_block_till_done()
    assert climates.pop_calls() == []


async def test_failed_command_is_sent_again(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """A failing device does not block the others and is tried again at the next read."""
    calendar.events = [meeting()]
    entry = await setup(hass)

    climates.errors[STAGE] = HomeAssistantError("timeout")
    await move(hass, freezer, entry, at(8))
    assert climates.pop_calls() == [(HALL, "heat"), (HALL, 21)]

    del climates.errors[STAGE]
    await move(hass, freezer, entry, at(8, 15))
    assert climates.pop_calls() == [(STAGE, "heat"), (STAGE, 21)]


async def test_refused_command_is_not_sent_again(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """A mode which the entity does not support is not tried again."""
    calendar.events = [meeting()]
    entry = await setup(hass)

    climates.errors[STAGE] = ServiceValidationError("unsupported mode")
    await move(hass, freezer, entry, at(8))
    del climates.errors[STAGE]
    await move(hass, freezer, entry, at(8, 15))
    assert climates.pop_calls() == [(HALL, "heat"), (HALL, 21)]


async def test_restart_during_preheat(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """After a restart in rooms which are already warm, the preheat goes on and the previous state is still known."""
    climates.set(HALL, target=19)
    calendar.events = [meeting()]
    entry = await setup(hass, **{CONF_END_HVAC_MODE: END_PREVIOUS})
    await move(hass, freezer, entry, at(8))
    assert climates.pop_calls() == EVENT_CALLS

    climates.set(HALL, current=21, target=21)
    climates.set(STAGE, current=21, target=21)
    freezer.move_to(at(9))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(SENSOR).state == "preheat"
    assert climates.pop_calls() == []

    await move(hass, freezer, entry, at(12))
    assert climates.pop_calls() == [
        (HALL, "heat"),
        (HALL, 19),
        (STAGE, "heat"),
        (STAGE, 17),
    ]


async def test_restart_after_the_event(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """An event which ended while Home Assistant was stopped is ended at startup."""
    calendar.events = [meeting()]
    entry = await setup(hass)
    await move(hass, freezer, entry, at(8))
    climates.pop_calls()

    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(at(13))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(SENSOR).state == "idle"
    assert climates.pop_calls() == END_CALLS


async def test_removed_entity_is_forgotten(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """An entity removed from the options during an event is left alone."""
    calendar.events = [meeting()]
    entry = await setup(hass, **{CONF_END_HVAC_MODE: END_PREVIOUS})
    await move(hass, freezer, entry, at(8))
    climates.pop_calls()

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_CLIMATE_ENTITIES: [HALL]}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    await move(hass, freezer, entry, at(12))
    assert climates.pop_calls() == [(HALL, "heat"), (HALL, 17)]


async def test_timers_follow_the_schedule(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """The wake-ups and the periodic read follow the schedule without any other trigger."""
    calendar.events = [meeting()]
    await setup(hass)

    async def elapse(when: datetime) -> None:
        freezer.move_to(when)
        async_fire_time_changed(hass, when)
        await hass.async_block_till_done(wait_background_tasks=True)

    await elapse(at(7, 59))
    assert hass.states.get(SENSOR).state == "idle"
    await elapse(at(8))
    assert hass.states.get(SENSOR).state == "preheat"
    assert climates.pop_calls() == EVENT_CALLS

    await elapse(at(10))
    assert hass.states.get(SENSOR).state == "event"

    calendar.events = []
    await elapse(at(10, 15))
    assert hass.states.get(SENSOR).state == "event"
    await elapse(at(10, 30))
    assert hass.states.get(SENSOR).state == "idle"
    assert climates.pop_calls() == END_CALLS


async def test_calendar_change_is_read_at_once(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
) -> None:
    """A state change of the calendar entity triggers a read."""
    await setup(hass)
    freezer.move_to(at(9, 30))

    calendar.events = [meeting()]
    hass.states.async_set(CALENDAR, "off", {"message": "Meeting"})
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "preheat"
    assert climates.pop_calls() == EVENT_CALLS


async def test_calendar_loaded_after_the_schedule(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, climates: FakeClimates
) -> None:
    """A calendar which does not exist yet is read as soon as it appears."""
    await setup(hass)
    assert hass.states.get(SENSOR).attributes["calendar_available"] is False

    calendar = MockCalendar()
    calendar.events = [meeting(start=(7, 0), end=(8, 0))]
    await hass.data["calendar"].async_add_entities([calendar])
    await hass.async_block_till_done()

    assert hass.states.get(SENSOR).state == "preheat"
    assert climates.pop_calls() == EVENT_CALLS


async def test_remove_entry_removes_the_stored_schedule(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    calendar: MockCalendar,
    climates: FakeClimates,
    hass_storage: dict[str, Any],
) -> None:
    """The stored schedule does not outlive its config entry."""
    calendar.events = [meeting()]
    entry = await setup(hass)
    await move(hass, freezer, entry, at(8))
    key = f"calendar_climate.{entry.entry_id}"
    assert hass_storage[key]["data"]["target"] is not None

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
