"""Owns the listening sockets and wires discovery, the MQTT gate and the unit tracker together."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import ipaddress
import logging

from . import tls
from .const import DIS_PLAIN_PORT, DIS_PORT, GATE_PORT, dis_host, mqtt_host
from .discovery import DiscoveryService
from .dns import ExternalResolver
from .gate import MqttGate
from .units import UnitTracker

_LOGGER = logging.getLogger(__name__)

BIND_ADDRESS = "0.0.0.0"
Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def parse_networks(value: str) -> tuple[Network, ...]:
    """Comma-separated CIDRs; raises ValueError on a bad one."""
    return tuple(
        ipaddress.ip_network(item.strip(), strict=False)
        for item in value.split(",")
        if item.strip()
    )


class GreeServer:
    def __init__(
        self,
        units: UnitTracker,
        *,
        advertised_ip: str,
        allowed_networks: Sequence[Network],
        cert_dir: str,
        region: str,
        resolvers: Sequence[str],
        sink_delay: float,
    ) -> None:
        self.units = units
        self.advertised_ip = advertised_ip
        self._allowed_networks = tuple(allowed_networks)
        self._cert_dir = cert_dir
        self._listeners: list[asyncio.Server] = []
        resolver = ExternalResolver(resolvers)
        self._discovery = DiscoveryService(
            advertised_ip=advertised_ip, dis_host=dis_host(region), resolver=resolver, units=units
        )
        self._gate = MqttGate(
            mqtt_host=mqtt_host(region), resolver=resolver, sink_delay=sink_delay, units=units
        )

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        ssl_ctx = await loop.run_in_executor(None, tls.server_context, self._cert_dir)
        endpoints = (
            (self._discovery.handle, DIS_PORT, ssl_ctx),
            (self._discovery.handle, DIS_PLAIN_PORT, None),
            (self._gate.handle, GATE_PORT, None),
        )
        try:
            for handler, port, ctx in endpoints:
                self._listeners.append(
                    await asyncio.start_server(self._guarded(handler), BIND_ADDRESS, port, ssl=ctx)
                )
        except OSError:
            await self.stop()
            raise
        _LOGGER.info(
            "listening on %s:%d/%d, gate :%d",
            BIND_ADDRESS,
            DIS_PORT,
            DIS_PLAIN_PORT,
            GATE_PORT,
        )

    async def stop(self) -> None:
        for listener in self._listeners:
            listener.close()
            # wait_closed() also waits for open connections, which units hold for minutes.
            listener.close_clients()
        for listener in self._listeners:
            await listener.wait_closed()
        self._listeners = []

    def _guarded(self, handler: Handler) -> Handler:
        async def guarded(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            peer = ipaddress.ip_address(writer.get_extra_info("peername")[0])
            if not any(peer in network for network in self._allowed_networks):
                _LOGGER.debug("rejected connection from %s", peer)
                writer.close()
                return
            await handler(reader, writer)

        return guarded
