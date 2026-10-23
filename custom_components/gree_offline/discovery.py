# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local stand-in for Gree's discovery server (<region>.dis.gree.com)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from homeassistant.util.ssl import SSLCipherList, client_context_no_verify

from .const import DIS_PLAIN_PORT, DIS_PORT, GATE_PORT, GENERIC_KEY
from .crypto import encrypt, to_json, unpack
from .dns import ExternalResolver
from .units import UnitStats, UnitTracker

_LOGGER = logging.getLogger(__name__)

IDLE_TIMEOUT = 300
UPSTREAM_TIMEOUT = 10


class DiscoveryService:
    """Answers units with our own addresses; asks Gree only while a unit is being onboarded."""

    def __init__(
        self,
        *,
        advertised_ip: str,
        dis_host: str,
        resolver: ExternalResolver,
        units: UnitTracker,
    ) -> None:
        self.advertised_ip = advertised_ip
        self.dis_host = dis_host
        self.resolver = resolver
        self.units = units

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tls_used = writer.get_extra_info("cipher") is not None
        peer = writer.get_extra_info("peername")[0]
        try:
            while line := await asyncio.wait_for(reader.readline(), IDLE_TIMEOUT):
                request = unpack(json.loads(line), GENERIC_KEY)
                if request.get("t") == "dis":
                    mac = request.get("mac", "")
                    svr = await self._answer_dis(mac, request, line, tls_used, peer)
                    await _reply(writer, _packed(svr, mac))
                    return
                if (reply := _session_reply(request)) is None:
                    return
                await _reply(writer, reply)
        except (asyncio.IncompleteReadError, TimeoutError, ConnectionError, json.JSONDecodeError):
            pass
        except Exception as e:
            _LOGGER.debug("dis conn %s ended: %r", peer, e)
        finally:
            writer.close()

    async def _answer_dis(
        self, mac: str, request: dict, line: bytes, tls_used: bool, peer: str
    ) -> dict:
        stats = self.units.record_discovery(mac, peer)
        if self.units.upstream_allowed(mac):
            try:
                svr = await self._relayed_svr(line, tls_used, stats)
            except Exception as e:
                _LOGGER.warning("dis %s %s: upstream failed (%r), answering locally", mac, peer, e)
            else:
                _LOGGER.info("dis %s %s: onboarding, relayed, broker %s", mac, peer, stats.broker)
                return svr
        else:
            _LOGGER.info("dis %s %s: answered locally", mac, peer)
        return self._local_svr(tls_used, need_mqtt=bool(request.get("needMqtt")))

    async def _relayed_svr(self, line: bytes, tls_used: bool, stats: UnitStats) -> dict:
        """Gree's real answer with our addresses swapped in; its broker is kept for the gate."""
        svr = await self._fetch_upstream_svr(line)
        svr.update(self._local_addresses(tls_used))
        if mqtt := svr.get("mqttInfo"):
            stats.broker = (mqtt["host"], int(mqtt["port"]))
            mqtt["host"], mqtt["port"] = self.advertised_ip, GATE_PORT
        return svr

    async def _fetch_upstream_svr(self, line: bytes) -> dict:
        ip = await self.resolver.resolve(self.dis_host)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                ip,
                DIS_PORT,
                ssl=client_context_no_verify(SSLCipherList.INSECURE),
                server_hostname=self.dis_host,
            ),
            UPSTREAM_TIMEOUT,
        )
        try:
            writer.write(line)
            await writer.drain()
            reply = json.loads(await asyncio.wait_for(reader.readline(), UPSTREAM_TIMEOUT))
        finally:
            writer.close()
        return unpack(reply, GENERIC_KEY)

    def _local_addresses(self, tls_used: bool) -> dict[str, Any]:
        ip = self.advertised_ip
        port = DIS_PORT if tls_used else DIS_PLAIN_PORT
        return {
            "ip": ip,
            "ip2": ip,
            "Ip3": ip,
            "host": ip,
            "udpPort": port,
            "tcpPort": port,
            "protocol": "" if tls_used else "TCP",
            "datHost": "",
            "datHostPort": 0,
        }

    def _local_svr(self, tls_used: bool, need_mqtt: bool) -> dict:
        svr = {"t": "svr", **self._local_addresses(tls_used)}
        if need_mqtt:
            svr["mqttInfo"] = {
                "host": self.advertised_ip,
                "port": GATE_PORT,
                "username": "local",
                "password": "local",
            }
        return svr


def _session_reply(request: dict) -> dict | None:
    """Keep-alive traffic a unit sends once it takes us for its cloud; None ends the session."""
    kind = request.get("t")
    if kind == "devLogin":
        mac = request["mac"]
        return _packed({"t": "loginRes", "r": 200, "cid": _login_cid(mac), "uid": 0}, mac)
    if kind == "tm":
        return {"t": "tm", "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    if kind == "hb":
        return {"t": "hbok"}
    return None


def _packed(body: dict, mac: str) -> dict:
    return {
        "t": "pack",
        "i": 1,
        "uid": 0,
        "cid": "",
        "tcid": mac,
        "pack": encrypt(body, GENERIC_KEY),
    }


def _login_cid(mac: str) -> str:
    """The cloud id Gree assigns is a fixed shuffle of the MAC's bytes."""
    octets = [mac[i : i + 2] for i in range(0, 16, 2)]
    return "".join(octets[i] for i in (4, 7, 1, 5, 2, 0))


async def _reply(writer: asyncio.StreamWriter, message: dict) -> None:
    writer.write((to_json(message) + "\n").encode())
    await writer.drain()
