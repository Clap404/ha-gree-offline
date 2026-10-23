"""Base for the per-unit diagnostic entities, refreshed whenever the server reports activity."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import CONF_MAC, DOMAIN, SIGNAL_STATS
from .entries import hub_server
from .server import GreeServer


class GreeUnitEntity(Entity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        self._mac: str = entry.data[CONF_MAC]
        self._attr_unique_id = f"{self._mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._mac)}, name=entry.title, manufacturer="Gree"
        )

    @property
    def _server(self) -> GreeServer | None:
        return hub_server(self.hass)

    @property
    def available(self) -> bool:
        return self._server is not None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_STATS, self._on_stats))

    @callback
    def _on_stats(self, mac: str) -> None:
        if mac == self._mac:
            self.async_write_ha_state()
