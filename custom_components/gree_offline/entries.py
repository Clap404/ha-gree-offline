"""Config entries: the single server hub, plus one entry per AC unit."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_KIND, DOMAIN, KIND_UNIT, unit_unique_id
from .server import GreeServer


def is_unit(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_KIND) == KIND_UNIT


def unit_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    return [e for e in hass.config_entries.async_entries(DOMAIN) if is_unit(e)]


def unit_entry_for_mac(hass: HomeAssistant, mac: str) -> ConfigEntry | None:
    return hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unit_unique_id(mac))


def hub_entry(hass: HomeAssistant) -> ConfigEntry | None:
    return next((e for e in hass.config_entries.async_entries(DOMAIN) if not is_unit(e)), None)


def hub_server(hass: HomeAssistant) -> GreeServer | None:
    hub = hub_entry(hass)
    return getattr(hub, "runtime_data", None) if hub else None
