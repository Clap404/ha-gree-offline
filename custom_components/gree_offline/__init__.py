"""Gree Offline: local discovery server with a filtered MQTT gate towards Gree's cloud."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_ALLOWED_NETWORKS
from .entries import is_unit
from .hub import async_setup_hub, async_unload_hub, default_networks

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        options = dict(entry.options)
        if not is_unit(entry) and not options.get(CONF_ALLOWED_NETWORKS):
            # Allowed networks became required in 1.1.0.
            options[CONF_ALLOWED_NETWORKS] = await default_networks(hass)
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if is_unit(entry):
        # Unit entities read the hub's server lazily: entries are set up in parallel at startup.
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        return True
    return await async_setup_hub(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if is_unit(entry):
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return await async_unload_hub(entry)
