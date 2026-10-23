# SPDX-License-Identifier: AGPL-3.0-or-later
"""Push Wi-Fi credentials and the discovery host to a Gree unit over GATT, via HA Bluetooth."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import GENERIC_KEY, bare_mac, dis_host
from .crypto import decrypt, encrypt, to_json

_LOGGER = logging.getLogger(__name__)

CHAR_TX = "0000fd03-0000-1000-8000-00805f9b34fb"
CHAR_RX = "0000fd04-0000-1000-8000-00805f9b34fb"
# Value of the "i" field telling which key encrypts a pack.
KEY_ID_BLE = 0
KEY_ID_GENERIC = 1
MAX_BUFFER = 8192
BLEKEY_TIMEOUT = 15
WIFI_TIMEOUT = 90


class OnboardError(Exception):
    """Onboarding failed."""


class _Session:
    """JSON messages over GATT; notifications may split or merge messages, so they are buffered."""

    def __init__(self, client: BleakClient) -> None:
        self.client = client
        self.buffer = bytearray()
        self.ble_key: bytes | None = None
        self.waiters: dict[str, asyncio.Future[dict]] = {}

    def _key(self, key_id: int | None) -> bytes | None:
        if key_id == KEY_ID_GENERIC:
            return GENERIC_KEY
        if key_id == KEY_ID_BLE:
            return self.ble_key
        return None

    async def request(self, message: dict, key_id: int, reply_type: str) -> dict:
        """Send an encrypted message and wait for the reply of the given type."""
        reply = asyncio.get_running_loop().create_future()
        self.waiters[reply_type] = reply
        key = self._key(key_id)
        assert key is not None
        outer = {"t": "pack", "pack": encrypt(message, key), "i": key_id, "pIn": 0}
        await self.client.write_gatt_char(CHAR_TX, to_json(outer).encode(), response=True)
        return await reply

    def on_notify(self, _char: object, data: bytearray) -> None:
        self.buffer += data
        decoder = json.JSONDecoder()
        while True:
            text = self.buffer.decode(errors="replace")
            message_text = text.lstrip()
            if not message_text:
                self.buffer.clear()
                return
            try:
                message, end = decoder.raw_decode(message_text)
            except json.JSONDecodeError:
                if len(self.buffer) > MAX_BUFFER:
                    self.buffer.clear()
                return
            consumed = len(text) - len(message_text) + end
            del self.buffer[: len(text[:consumed].encode())]
            self._handle(message)

    def _handle(self, message: dict) -> None:
        kind: str = message.get("t", "")
        if kind == "pack":
            if (key := self._key(message.get("i"))) is None:
                return
            try:
                self._handle(decrypt(message["pack"], key))
            except Exception as e:
                _LOGGER.debug("decrypt failed: %r", e)
            return

        if kind == "blekey":
            self.ble_key = message["key"].encode()
        waiter = self.waiters.pop(kind, None)
        if waiter and not waiter.done():
            waiter.set_result(message)


async def async_onboard(
    hass: HomeAssistant,
    address: str,
    ssid: str,
    password: str,
    *,
    region: str,
) -> dict:
    """Run bind -> blekey -> wlan and return the unit's bleinfo; raises OnboardError."""
    device = bluetooth.async_ble_device_from_address(hass, address.upper(), connectable=True)
    if device is None:
        raise OnboardError(f"{address} not reachable over any Bluetooth adapter/proxy")

    try:
        client = await establish_connection(BleakClientWithServiceCache, device, address)
    except BleakError as e:
        raise OnboardError(f"cannot connect to {address}: {e!r}") from e
    session = _Session(client)
    try:
        await client.start_notify(CHAR_RX, session.on_notify)

        bind = {"t": "bind", "mac": bare_mac(address)[-4:], "IsCP": 1}
        try:
            async with asyncio.timeout(BLEKEY_TIMEOUT):
                await session.request(bind, KEY_ID_GENERIC, "blekey")
        except TimeoutError as e:
            raise OnboardError("no blekey (unit not in pairing mode?)") from e

        wlan = {"t": "wlan", "host": dis_host(region), "psw": password, "ssid": ssid, "num": 1}
        try:
            async with asyncio.timeout(WIFI_TIMEOUT):
                info = await session.request(wlan, KEY_ID_BLE, "bleinfo")
        except TimeoutError as e:
            raise OnboardError(f"no bleinfo within {WIFI_TIMEOUT}s (wrong Wi-Fi password?)") from e

        if info.get("wificon") != 1:
            raise OnboardError(f"unit reported wificon={info.get('wificon')}")
        return info
    except BleakError as e:
        raise OnboardError(f"Bluetooth error with {address}: {e!r}") from e
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
