"""The Calendar Climate integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .scheduler import CalendarClimateConfigEntry, CalendarClimateScheduler

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: CalendarClimateConfigEntry
) -> None:
    """Set up Calendar Climate from a config entry."""
    scheduler = CalendarClimateScheduler(hass, entry)
    entry.runtime_data = scheduler
    await scheduler.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(
    hass: HomeAssistant, entry: CalendarClimateConfigEntry
) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: CalendarClimateConfigEntry
) -> None:
    """Remove the stored schedule of a removed config entry."""
    await CalendarClimateScheduler.async_remove_store(hass, entry)
