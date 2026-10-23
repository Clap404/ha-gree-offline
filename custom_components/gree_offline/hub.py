"""Build the server from the hub entry's options and feed what it learns back into unit entries."""

from __future__ import annotations

from functools import partial
import logging

from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_ADVERTISED_IP,
    CONF_ALLOWED_NETWORKS,
    CONF_IP,
    CONF_LAST_CLOUD_HANDSHAKE,
    CONF_MAC,
    CONF_REGION,
    CONF_RESOLVER,
    CONF_SINK_DELAY,
    DEFAULT_REGION,
    DEFAULT_RESOLVERS,
    DEFAULT_SINK_DELAY,
    DOMAIN,
    SIGNAL_STATS,
)
from .entries import unit_entries, unit_entry_for_mac
from .server import GreeServer, parse_networks
from .units import UnitTracker

_LOGGER = logging.getLogger(__name__)


async def async_setup_hub(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    advertised_ip = await _advertised_ip(hass, entry)
    if not advertised_ip:
        _LOGGER.error("could not determine the IP to advertise; set it in the integration options")
        return False

    units = UnitTracker(
        on_update=partial(async_dispatcher_send, hass, SIGNAL_STATS),
        on_seen=partial(_on_unit_seen, hass, set()),
        on_handshake=partial(_on_unit_handshake, hass),
    )
    for unit in unit_entries(hass):
        units.seed(
            unit.data[CONF_MAC], unit.data.get(CONF_IP), unit.data.get(CONF_LAST_CLOUD_HANDSHAKE)
        )

    options = entry.options
    if not (allowed_networks := parse_networks(options.get(CONF_ALLOWED_NETWORKS, ""))):
        _LOGGER.error("no allowed networks; set them in the integration options")
        return False
    server = GreeServer(
        units,
        advertised_ip=advertised_ip,
        allowed_networks=allowed_networks,
        cert_dir=hass.config.path(DOMAIN),
        region=options.get(CONF_REGION, DEFAULT_REGION),
        resolvers=_split_list(options.get(CONF_RESOLVER, DEFAULT_RESOLVERS)),
        sink_delay=float(options.get(CONF_SINK_DELAY, DEFAULT_SINK_DELAY)),
    )
    try:
        await server.start()
    except OSError as e:
        _LOGGER.error("could not bind the discovery server (ports in use?): %r", e)
        return False

    entry.runtime_data = server
    for unit in unit_entries(hass):
        async_dispatcher_send(hass, SIGNAL_STATS, unit.data[CONF_MAC])
    _LOGGER.info("Gree Offline server up, advertising %s", advertised_ip)
    return True


async def async_unload_hub(entry: ConfigEntry) -> bool:
    _LOGGER.debug("unloading Gree Offline server")
    server: GreeServer | None = getattr(entry, "runtime_data", None)
    if server:
        await server.stop()
        entry.runtime_data = None
    return True


async def _advertised_ip(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    if ip := entry.options.get(CONF_ADVERTISED_IP):
        return ip
    try:
        return await async_get_source_ip(hass)
    except Exception:
        return None


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _on_unit_seen(hass: HomeAssistant, offered: set[str], mac: str, ip: str) -> None:
    """Keep a known unit's IP current, or offer to adopt an unknown one (once per run)."""
    unit = unit_entry_for_mac(hass, mac)
    if unit is not None:
        if unit.data.get(CONF_IP) != ip:
            hass.config_entries.async_update_entry(unit, data={**unit.data, CONF_IP: ip})
    elif mac not in offered:
        offered.add(mac)
        discovery_flow.async_create_flow(
            hass, DOMAIN, {"source": SOURCE_INTEGRATION_DISCOVERY}, {CONF_MAC: mac, CONF_IP: ip}
        )


def _on_unit_handshake(hass: HomeAssistant, mac: str, timestamp: float) -> None:
    if (unit := unit_entry_for_mac(hass, mac)) is not None:
        hass.config_entries.async_update_entry(
            unit, data={**unit.data, CONF_LAST_CLOUD_HANDSHAKE: timestamp}
        )
