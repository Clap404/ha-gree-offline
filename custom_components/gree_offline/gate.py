"""MQTT gate: a unit reaches Gree's broker only while being onboarded, otherwise it is held off."""

from __future__ import annotations

import asyncio
import logging

from .const import MQTT_PORT
from .dns import ExternalResolver
from .relay import Relay
from .units import UnitTracker

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10


class MqttGate:
    def __init__(
        self,
        *,
        mqtt_host: str,
        resolver: ExternalResolver,
        sink_delay: float,
        units: UnitTracker,
    ) -> None:
        self.mqtt_host = mqtt_host
        self.resolver = resolver
        self.sink_delay = sink_delay
        self.units = units

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")[0]
        mac = self.units.mac_for_ip(peer)
        if mac is not None:
            self.units.mark_seen(mac)
        if mac is None or not self.units.upstream_allowed(mac):
            await self._sink(reader, writer)
            return

        broker = self.units.stats(mac).broker or await self._fallback_broker()
        try:
            gree = await asyncio.wait_for(asyncio.open_connection(*broker), CONNECT_TIMEOUT)
        except Exception as e:
            _LOGGER.warning("gate %s cannot reach broker %s: %r", peer, broker, e)
            writer.close()
            return

        self.units.record_handshake(mac)
        _LOGGER.info("gate %s %s -> %s", peer, mac, broker)
        await Relay(peer, (reader, writer), gree).run()

    async def _sink(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Close fast: holding a connection mid-handshake blocks the unit's UDP control."""
        try:
            await asyncio.wait_for(reader.read(4096), 10)
            await asyncio.sleep(self.sink_delay)
        except Exception:
            pass
        finally:
            writer.close()

    async def _fallback_broker(self) -> tuple[str, int]:
        try:
            return (await self.resolver.resolve(self.mqtt_host), MQTT_PORT)
        except Exception:
            return (self.mqtt_host, MQTT_PORT)
