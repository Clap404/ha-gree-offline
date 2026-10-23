"""Per-unit state shared by the discovery server and the MQTT gate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time

from .const import ACTIVE_WINDOW


@dataclass
class UnitStats:
    last_seen: float | None = None
    last_cloud_handshake: float | None = None
    broker: tuple[str, int] | None = None

    def is_active(self) -> bool:
        return self.last_seen is not None and time.time() - self.last_seen < ACTIVE_WINDOW


class UnitTracker:
    """Records what each unit does and tells the listeners about it."""

    def __init__(
        self,
        *,
        on_update: Callable[[str], None],
        on_seen: Callable[[str, str], None],
        on_handshake: Callable[[str, float], None],
    ) -> None:
        self._stats: dict[str, UnitStats] = {}
        self._mac_by_ip: dict[str, str] = {}
        self._upstream_until: dict[str, float] = {}
        self._on_update = on_update
        self._on_seen = on_seen
        self._on_handshake = on_handshake

    def stats(self, mac: str) -> UnitStats:
        return self._stats.setdefault(mac, UnitStats())

    def is_active(self, mac: str) -> bool:
        stats = self._stats.get(mac)
        return stats is not None and stats.is_active()

    def mac_for_ip(self, ip: str) -> str | None:
        return self._mac_by_ip.get(ip)

    def seed(self, mac: str, ip: str | None, last_cloud_handshake: float | None) -> None:
        """Restore what was persisted about a known unit before the restart."""
        if ip:
            self._mac_by_ip.setdefault(ip, mac)
        stats = self.stats(mac)
        stats.last_cloud_handshake = stats.last_cloud_handshake or last_cloud_handshake

    # Onboarding window: the only time a unit may reach Gree.

    def allow_upstream(self, mac: str, seconds: float) -> None:
        self._upstream_until[mac] = time.monotonic() + seconds

    def end_upstream(self, mac: str) -> None:
        self._upstream_until.pop(mac, None)

    def upstream_allowed(self, mac: str) -> bool:
        return time.monotonic() < self._upstream_until.get(mac, 0.0)

    # Activity

    def mark_seen(self, mac: str) -> UnitStats:
        stats = self.stats(mac)
        was_active = stats.is_active()
        stats.last_seen = time.time()
        if not was_active:
            self._on_update(mac)
        return stats

    def record_discovery(self, mac: str, ip: str) -> UnitStats:
        self._mac_by_ip[ip] = mac
        stats = self.stats(mac)
        stats.last_seen = time.time()
        self._on_seen(mac, ip)
        self._on_update(mac)
        return stats

    def record_handshake(self, mac: str) -> UnitStats:
        stats = self.stats(mac)
        stats.last_seen = stats.last_cloud_handshake = time.time()
        self._on_handshake(mac, stats.last_cloud_handshake)
        self._on_update(mac)
        return stats
