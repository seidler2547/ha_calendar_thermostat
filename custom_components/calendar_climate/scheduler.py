"""Follow the events of a calendar and drive climate entities."""

import asyncio
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import logging
from typing import Any

from homeassistant.components.calendar import (
    DOMAIN as CALENDAR_DOMAIN,
    EVENT_END_DATETIME,
    EVENT_START_DATETIME,
    SERVICE_GET_EVENTS,
)
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

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
    PREHEAT_MODES,
    REFRESH_INTERVAL,
    SETPOINT_MODES,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

type CalendarClimateConfigEntry = ConfigEntry[CalendarClimateScheduler]


class ScheduleState(StrEnum):
    """What the schedule is doing."""

    IDLE = "idle"
    PREHEAT = "preheat"
    EVENT = "event"


@dataclass(frozen=True, slots=True)
class Block:
    """A time span covered by one or several contiguous calendar events."""

    start: datetime
    end: datetime

    def overlaps(self, other: Block) -> bool:
        """Return True if both spans share some time."""
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class Action:
    """The mode and the temperature sent to a climate entity."""

    hvac_mode: str
    temperature: float | None = None


def merge_events(spans: Iterable[tuple[datetime, datetime]]) -> list[Block]:
    """Merge overlapping and contiguous events into blocks sorted by start."""
    blocks: list[Block] = []
    for start, end in sorted(span for span in spans if span[0] < span[1]):
        if blocks and start <= blocks[-1].end:
            blocks[-1] = Block(blocks[-1].start, max(blocks[-1].end, end))
        else:
            blocks.append(Block(start, end))
    return blocks


def _parse(value: str) -> datetime:
    """Parse the start or the end of an event. An all-day event uses local midnight."""
    return dt_util.as_local(dt_util.parse_datetime(value, raise_on_error=True))


def _is_available(state: State | None) -> bool:
    return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)


def _get_store(
    hass: HomeAssistant, entry: CalendarClimateConfigEntry
) -> Store[dict[str, Any]]:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")


class CalendarClimateScheduler:
    """Apply a mode and a temperature to climate entities around calendar events.

    The event action is sent when the preheat or the event starts and the end
    action when the event ends. Nothing is sent in between, so a manual change
    is kept until the next transition.
    """

    def __init__(self, hass: HomeAssistant, entry: CalendarClimateConfigEntry) -> None:
        """Read the options of the config entry."""
        self.hass = hass
        self.entry = entry
        self.signal = f"{DOMAIN}_{entry.entry_id}"

        options = entry.options
        self.calendar_entity_id: str = options[CONF_CALENDAR_ENTITY]
        self.climate_entity_ids: list[str] = options[CONF_CLIMATE_ENTITIES]
        self._event_action = Action(
            options[CONF_EVENT_HVAC_MODE], options.get(CONF_EVENT_TEMPERATURE)
        )
        # None restores the mode and temperature each entity had before the event
        self._end_action = (
            None
            if options[CONF_END_HVAC_MODE] == END_PREVIOUS
            else Action(options[CONF_END_HVAC_MODE], options.get(CONF_END_TEMPERATURE))
        )
        self._gradient: float = options[CONF_PREHEAT_GRADIENT]
        self._max_preheat = timedelta(**options[CONF_MAX_PREHEAT])

        self.calendar_available = False
        # The block being preheated or running. It is kept once set, whatever
        # the temperature does.
        self.target: Block | None = None
        self.missing_since: datetime | None = None
        self.upcoming: Block | None = None
        self.preheat_start: datetime | None = None
        self.estimated_ready_time: datetime | None = None

        self._blocks: list[Block] = []
        # True when the calendar has been read since the last evaluation
        self._is_fresh = False
        self._previous: dict[str, Action] = {}
        # Entities which did not get the action of the last transition yet
        self._pending: set[str] = set()

        self._store = _get_store(hass, entry)
        self._saved: dict[str, Any] | None = None
        self._cancel_wakeup: CALLBACK_TYPE | None = None
        self._stopped = False
        self._read_lock = asyncio.Lock()
        self._evaluate_lock = asyncio.Lock()

    @property
    def state(self) -> ScheduleState:
        """Return what the schedule is doing."""
        if self.target is None:
            return ScheduleState.IDLE
        if dt_util.now() < self.target.start:
            return ScheduleState.PREHEAT
        return ScheduleState.EVENT

    async def async_start(self) -> None:
        """Restore the running schedule and start following the calendar."""
        if stored := await self._store.async_load():
            self._restore(stored)

        entry = self.entry
        entry.async_on_unload(
            async_track_state_change_event(
                self.hass, self.calendar_entity_id, self.async_refresh
            )
        )
        entry.async_on_unload(
            async_track_state_change_event(
                self.hass, self.climate_entity_ids, self._async_climate_changed
            )
        )
        entry.async_on_unload(
            async_track_time_interval(self.hass, self.async_refresh, REFRESH_INTERVAL)
        )
        entry.async_on_unload(self._async_stop)
        # The setup does not wait for the calendar
        entry.async_create_background_task(
            self.hass, self.async_refresh(), f"{DOMAIN} first read"
        )

    @staticmethod
    async def async_remove_store(
        hass: HomeAssistant, entry: CalendarClimateConfigEntry
    ) -> None:
        """Remove the stored schedule of a config entry."""
        await _get_store(hass, entry).async_remove()

    async def async_refresh(self, _: Any = None) -> None:
        """Read the calendar and follow the schedule."""
        async with self._read_lock:
            blocks = await self._async_read_blocks()
            if blocks is not None:
                self._blocks, self._is_fresh = blocks, True
            self.calendar_available = blocks is not None

        await self._async_evaluate(retry_pending=True)

    async def _async_read_blocks(self) -> list[Block] | None:
        """Read the upcoming events.

        Return None if the calendar cannot be read: the known events are kept.
        """
        if not _is_available(self.hass.states.get(self.calendar_entity_id)):
            return None

        now = dt_util.now()
        try:
            response = await self.hass.services.async_call(
                CALENDAR_DOMAIN,
                SERVICE_GET_EVENTS,
                {
                    ATTR_ENTITY_ID: self.calendar_entity_id,
                    EVENT_START_DATETIME: now,
                    EVENT_END_DATETIME: now + self._max_preheat + 2 * REFRESH_INTERVAL,
                },
                blocking=True,
                return_response=True,
            )
            events = response[self.calendar_entity_id]["events"]
            return merge_events(
                (_parse(event["start"]), _parse(event["end"])) for event in events
            )
        except Exception as err:
            _LOGGER.log(
                logging.WARNING if self.calendar_available else logging.DEBUG,
                "%s: cannot read the events of %s: %r",
                self.entry.title,
                self.calendar_entity_id,
                err,
            )
            return None

    async def _async_climate_changed(self, event: Event[EventStateChangedData]) -> None:
        """Plan the preheat again and catch up an entity which is back."""
        await self._async_evaluate(
            retry_pending=event.data["entity_id"] in self._pending
        )

    async def _async_evaluate(self, *, retry_pending: bool = False) -> None:
        """Follow the schedule from the events already read."""
        async with self._evaluate_lock:
            if self._stopped:
                return

            now = dt_util.now()
            blocks = [block for block in self._blocks if block.end > now]
            previous_target = target = self.target

            if target:
                if match := next((b for b in blocks if b.overlaps(target)), None):
                    target, self.missing_since = match, None
                elif self._is_fresh:
                    self.missing_since = self.missing_since or now
                    if now - self.missing_since >= REFRESH_INTERVAL:
                        target = None
                if target and target.end <= now:
                    target = None
            self._is_fresh = False

            heating_duration = self._heating_duration()
            self.upcoming = self.preheat_start = None
            if target is None:
                self.missing_since = None
                if blocks:
                    self.upcoming = blocks[0]
                    self.preheat_start = self.upcoming.start - min(
                        heating_duration, self._max_preheat
                    )
                    if self.preheat_start <= now:
                        target, self.upcoming, self.preheat_start = (
                            blocks[0],
                            None,
                            None,
                        )

            self.target = target
            self.estimated_ready_time = (
                now + heating_duration if target and heating_duration else None
            )
            self._schedule_wakeup(now)

            if bool(target) != bool(previous_target):
                _LOGGER.info(
                    "%s: %s",
                    self.entry.title,
                    f"{self.state} started for the event from {target.start} to {target.end}"
                    if target
                    else "event ended",
                )
                self._pending = set(self.climate_entity_ids)
                retry_pending = True

            if retry_pending and self._pending:
                await asyncio.gather(
                    *(
                        self._async_apply(entity_id)
                        for entity_id in tuple(self._pending)
                    )
                )

            await self._async_save()
            async_dispatcher_send(self.hass, self.signal)

    def _heating_duration(self) -> timedelta:
        """Return the time needed by the coldest room to reach the event temperature."""
        action = self._event_action
        if (
            self._gradient <= 0
            or action.temperature is None
            or action.hvac_mode not in PREHEAT_MODES
        ):
            return timedelta(0)

        temperatures = [
            float(temperature)
            for entity_id in self.climate_entity_ids
            if _is_available(state := self.hass.states.get(entity_id))
            and (temperature := state.attributes.get(ATTR_CURRENT_TEMPERATURE))
            is not None
        ]
        if not temperatures:
            return timedelta(0)
        return timedelta(
            hours=max(action.temperature - min(temperatures), 0) / self._gradient
        )

    async def _async_apply(self, entity_id: str) -> None:
        """Send the action of the last transition to an entity, if it is available."""
        state = self.hass.states.get(entity_id)
        if not _is_available(state):
            return

        action: Action | None
        if self.target:
            if self._end_action is None:
                # A snapshot which has not been restored yet is the one to keep
                self._previous.setdefault(
                    entity_id,
                    Action(state.state, state.attributes.get(ATTR_TEMPERATURE)),
                )
            action = self._event_action
        else:
            action = self._end_action or self._previous.get(entity_id)

        if action is None or await self._async_send(entity_id, action):
            self._pending.discard(entity_id)
            if not self.target:
                self._previous.pop(entity_id, None)

    async def _async_send(self, entity_id: str, action: Action) -> bool:
        """Send a mode and a temperature. Return False if it is worth trying again."""
        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: action.hvac_mode},
                blocking=True,
            )
            if action.temperature is not None and action.hvac_mode in SETPOINT_MODES:
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    SERVICE_SET_TEMPERATURE,
                    {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: action.temperature},
                    blocking=True,
                )
        except ServiceValidationError as err:
            _LOGGER.warning(
                "%s: %s refused %s: %s", self.entry.title, entity_id, action, err
            )
            return True
        except Exception as err:
            _LOGGER.warning(
                "%s: %s failed on %s: %r", self.entry.title, entity_id, action, err
            )
            return False
        return True

    def _schedule_wakeup(self, now: datetime) -> None:
        """Read the calendar again at the next expected transition."""
        self._clear_wakeup()
        if target := self.target:
            wakeups = [target.start, target.end]
            if self.missing_since:
                wakeups.append(self.missing_since + REFRESH_INTERVAL)
        else:
            wakeups = [self.preheat_start] if self.preheat_start else []

        if wakeups := [wakeup for wakeup in wakeups if wakeup > now]:
            self._cancel_wakeup = async_track_point_in_time(
                self.hass, self.async_refresh, min(wakeups)
            )

    def _clear_wakeup(self) -> None:
        if self._cancel_wakeup:
            self._cancel_wakeup()
            self._cancel_wakeup = None

    @callback
    def _async_stop(self) -> None:
        self._stopped = True
        self._clear_wakeup()

    async def _async_save(self) -> None:
        """Store what a restart must not forget."""
        data = {
            "target": asdict(self.target) if self.target else None,
            "missing_since": self.missing_since,
            "previous": {
                entity_id: asdict(action)
                for entity_id, action in self._previous.items()
            },
            "pending": sorted(self._pending),
        }
        if data != self._saved:
            self._saved = data
            await self._store.async_save(data)

    def _restore(self, data: dict[str, Any]) -> None:
        """Restore the running block so that a restart neither stops nor restarts it."""
        try:
            target = data["target"]
            missing_since = data["missing_since"]
            previous = {
                entity_id: Action(**action)
                for entity_id, action in data["previous"].items()
                if entity_id in self.climate_entity_ids
            }
            pending = set(data["pending"]) & set(self.climate_entity_ids)
            block = (
                Block(_parse(target["start"]), _parse(target["end"]))
                if target
                else None
            )
            missing_since = _parse(missing_since) if missing_since else None
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "%s: ignoring the stored schedule: %r", self.entry.title, err
            )
            return

        self.target, self.missing_since = block, missing_since
        self._previous, self._pending = previous, pending
