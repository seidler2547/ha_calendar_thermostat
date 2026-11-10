# Calendar Climate

A Home Assistant integration which drives climate entities from a calendar: a mode and a temperature are set while an event is running, and another mode and temperature when it ends. With a preheat gradient, the heating starts early enough for the room to be warm when the event starts.

## Installation

Add this repository to [HACS](https://hacs.xyz) as a custom repository of type *Integration*, install **Calendar Climate** and restart Home Assistant. Without HACS, copy `custom_components/calendar_climate` into the `custom_components` folder of your configuration.

Requires Home Assistant 2026.11 or newer.

## Configuration

Add a schedule in **Settings → Devices & services → Helpers → Create helper → Calendar Climate**. Create one schedule per room or zone.

| Option | Description |
| ------ | ----------- |
| **Calendar** | The calendar to follow. All its events are used. |
| **Climate entities** | They all get the same mode and temperature. |
| **Mode / temperature during an event** | Sent when the preheat or the event starts. |
| **Mode / temperature after an event** | Sent when the event ends. **Previous state** restores the mode and temperature each entity had before the event. |
| **Preheat gradient** | Temperature gained by the room per hour of heating. `0` disables the preheat. |
| **Maximum preheat duration** | The preheat never starts earlier than this before an event. |

A temperature is required for the modes heat, cool, heat/cool and auto.

## Preheat

The preheat starts `(event temperature - room temperature) / gradient` hours before the event. At 3°/h, a room at 15° and an event at 21° start heating 2 hours before the event.

- The room temperature is the `current_temperature` of the climate entities. The coldest one decides for all.
- The start is planned again at each temperature change, until the preheat has started. A started preheat is kept until the event ends, even if the room heats faster than predicted.
- An event known too late is preheated at once.
- The preheat applies to the modes heat, heat/cool and auto.

The gradient is a fixed value: choose a pessimistic one, measured on a cold day.

## Behaviour

- **Transitions only**: the settings are sent when the preheat starts and when the event ends. A manual change in between is kept until the next transition.
- **Calendar**: it is read every 15 minutes, at each change of the calendar entity and at each planned transition. Overlapping and contiguous events are one event; an all-day event runs from midnight to midnight.
- **Removed event**: the heating goes on until the event is still missing 15 minutes later. An event moved onto its previous time span is followed without interruption.
- **Unavailable calendar**: the known events are kept and run until their known end.
- **Unavailable climate entity**: it gets the settings of the last transition when it is back. A failed command is sent again at the next read of the calendar.
- **Restart**: a running preheat or event, and the states to restore, survive a restart of Home Assistant.

## Sensor

Each schedule has a sensor with the state `idle`, `preheat` or `event` and the attributes `event_start`, `event_end`, `next_event_start`, `preheat_start`, `estimated_ready_time` and `calendar_available`.

## Development

```bash
uv venv --python 3.14
uv pip install -r requirements_test.txt
.venv/bin/pytest
```
