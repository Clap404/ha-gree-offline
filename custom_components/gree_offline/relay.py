"""Forward a unit's TLS session to Gree's broker just long enough to CONNECT and SUBSCRIBE."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from .const import GATE_HOLD, GATE_WINDOW

_LOGGER = logging.getLogger(__name__)

TLS_APPLICATION_DATA = 23
TLS_HEADER_SIZE = 5
# TLS payload sizes (MQTT packet + 24 bytes of GCM overhead) that may cross the gate.
# The first application record each way (CONNECT 88 / CONNACK 28) always passes.
MAX_UNIT_PAYLOAD = 51  # SUBSCRIBE 51; status PUBLISH is >= 700
MAX_GREE_PAYLOAD = 29  # CONNACK/PUBACK 28, SUBACK 29

Stream = tuple[asyncio.StreamReader, asyncio.StreamWriter]


class Relay:
    """Forward records both ways until either side sends something past the allowed prefix.

    Gree's side is closed after that (or GATE_WINDOW); the unit's side is drained and held open
    until GATE_HOLD so it does not reconnect right away.
    """

    def __init__(self, peer: str, unit: Stream, gree: Stream) -> None:
        self.peer = peer
        self.unit_reader, self.unit_writer = unit
        self.gree_reader, self.gree_writer = gree
        self.forwarding = True

    async def run(self) -> None:
        started = time.monotonic()
        unit_leg = asyncio.create_task(self._unit_to_gree())
        try:
            await asyncio.wait_for(self._gree_to_unit(), GATE_WINDOW)
        except Exception as e:
            _LOGGER.debug("gate %s upstream leg ended: %r", self.peer, e)
        finally:
            self.forwarding = False
            self.gree_writer.close()

        try:
            with contextlib.suppress(Exception):
                hold = max(0.0, GATE_HOLD - (time.monotonic() - started))
                await asyncio.wait_for(unit_leg, hold)
        finally:
            unit_leg.cancel()
            self.unit_writer.close()

    async def _unit_to_gree(self) -> None:
        allowed = _PrefixFilter(MAX_UNIT_PAYLOAD)
        with contextlib.suppress(asyncio.IncompleteReadError, ConnectionError, OSError):
            while True:
                record = await _read_tls_record(self.unit_reader)
                if not self.forwarding:
                    continue
                if not allowed(record):
                    self.forwarding = False
                    continue
                self.gree_writer.write(record)
                await self.gree_writer.drain()

    async def _gree_to_unit(self) -> None:
        allowed = _PrefixFilter(MAX_GREE_PAYLOAD)
        while True:
            record = await _read_tls_record(self.gree_reader)
            if not allowed(record):
                _LOGGER.info(
                    "gate %s cut at gree->unit record len=%d", self.peer, _payload_size(record)
                )
                return
            self.unit_writer.write(record)
            await self.unit_writer.drain()


class _PrefixFilter:
    """Passes records until an application record, other than the first, exceeds max_payload."""

    def __init__(self, max_payload: int) -> None:
        self.max_payload = max_payload
        self.app_records = 0

    def __call__(self, record: bytes) -> bool:
        if record[0] != TLS_APPLICATION_DATA:
            return True
        self.app_records += 1
        return self.app_records == 1 or _payload_size(record) <= self.max_payload


async def _read_tls_record(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(TLS_HEADER_SIZE)
    return header + await reader.readexactly(int.from_bytes(header[3:5], "big"))


def _payload_size(record: bytes) -> int:
    return len(record) - TLS_HEADER_SIZE
