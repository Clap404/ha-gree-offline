"""Per-unit timestamp of the last time the unit reached Gree's cloud (only while onboarding)."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import GreeUnitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([LastCloudHandshake(entry, "last_cloud_handshake")])


class LastCloudHandshake(GreeUnitEntity, SensorEntity):
    _attr_should_poll = False
    _attr_name = "Last cloud handshake"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        if (server := self._server) is None:
            return None
        timestamp = server.units.stats(self._mac).last_cloud_handshake
        return datetime.fromtimestamp(timestamp, tz=UTC) if timestamp else None
