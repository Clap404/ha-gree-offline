"""Config flow: a single server hub, plus per-unit onboarding from Bluetooth discovery."""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.network import async_get_adapters
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode
import voluptuous as vol

from .ble import OnboardError, async_onboard
from .const import (
    CONF_ADVERTISED_IP,
    CONF_ALLOWED_NETWORKS,
    CONF_IP,
    CONF_KIND,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_RESOLVER,
    CONF_SINK_DELAY,
    CONF_SSID,
    DEFAULT_REGION,
    DEFAULT_RESOLVERS,
    DEFAULT_SINK_DELAY,
    DOMAIN,
    KIND_HUB,
    KIND_UNIT,
    ONBOARD_UPSTREAM_WINDOW,
    REGIONS,
    bare_mac,
    unit_unique_id,
)
from .entries import hub_entry, hub_server
from .server import GreeServer, parse_networks

_LOGGER = logging.getLogger(__name__)

REGION_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=REGIONS, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
)

WIFI_SCHEMA = vol.Schema({vol.Required(CONF_SSID): str, vol.Required(CONF_PASSWORD): str})


def _hub_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_REGION, default=current.get(CONF_REGION, DEFAULT_REGION)
            ): REGION_SELECTOR,
            vol.Optional(CONF_ADVERTISED_IP, default=current.get(CONF_ADVERTISED_IP, "")): str,
            vol.Required(
                CONF_ALLOWED_NETWORKS, default=current.get(CONF_ALLOWED_NETWORKS, "")
            ): str,
            vol.Optional(CONF_RESOLVER, default=current.get(CONF_RESOLVER, DEFAULT_RESOLVERS)): str,
            vol.Optional(
                CONF_SINK_DELAY, default=current.get(CONF_SINK_DELAY, DEFAULT_SINK_DELAY)
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
        }
    )


async def _default_networks(hass: HomeAssistant) -> str:
    """Subnet of Home Assistant's default network adapter."""
    for adapter in await async_get_adapters(hass):
        if adapter["default"] and adapter["ipv4"]:
            ipv4 = adapter["ipv4"][0]
            return str(
                ipaddress.ip_network(f"{ipv4['address']}/{ipv4['network_prefix']}", strict=False)
            )
    return ""


def _hub_errors(user_input: dict[str, Any]) -> dict[str, str]:
    try:
        if parse_networks(user_input[CONF_ALLOWED_NETWORKS]):
            return {}
    except ValueError:
        pass
    return {CONF_ALLOWED_NETWORKS: "invalid_networks"}


def _unit_data(mac: str, ip: str | None = None) -> dict[str, Any]:
    data = {CONF_KIND: KIND_UNIT, CONF_MAC: mac}
    if ip:
        data[CONF_IP] = ip
    return data


class GreeOfflineConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._bluetooth: BluetoothServiceInfoBleak | None = None
        self._seen_mac = ""
        self._seen_ip = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GreeOfflineOptionsFlow()

    # Hub

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual setup of the server hub."""
        if hub_entry(self.hass) is not None:
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None and not (errors := _hub_errors(user_input)):
            return self.async_create_entry(
                title="Gree Offline server", data={CONF_KIND: KIND_HUB}, options=user_input
            )
        return self.async_show_form(
            step_id="user",
            data_schema=_hub_schema(
                user_input or {CONF_ALLOWED_NETWORKS: await _default_networks(self.hass)}
            ),
            errors=errors,
        )

    # Unit already on Wi-Fi

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """A unit already on Wi-Fi contacted the server."""
        self._seen_mac, self._seen_ip = discovery_info[CONF_MAC], discovery_info[CONF_IP]
        _LOGGER.debug("adoption offer for %s at %s", self._seen_mac, self._seen_ip)
        await self.async_set_unique_id(unit_unique_id(self._seen_mac))
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": f"{self._seen_mac} ({self._seen_ip})"}
        return await self.async_step_adopt()

    async def async_step_adopt(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=f"Gree {self._seen_mac}", data=_unit_data(self._seen_mac, self._seen_ip)
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="adopt", description_placeholders={"mac": self._seen_mac, "ip": self._seen_ip}
        )

    # Unit in pairing mode

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """A unit in pairing mode is advertising over Bluetooth."""
        _LOGGER.debug("bluetooth discovery %s (%s)", discovery_info.address, discovery_info.name)
        await self.async_set_unique_id(unit_unique_id(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._bluetooth = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name or discovery_info.address}
        return await self.async_step_onboard()

    async def async_step_onboard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._bluetooth is not None
        address = self._bluetooth.address
        if (hub := hub_entry(self.hass)) is None:
            _LOGGER.debug("onboard %s: no hub entry, aborting", address)
            return self.async_abort(reason="no_hub")

        errors: dict[str, str] = {}
        if user_input is not None:
            _LOGGER.debug("onboard %s: submitted for SSID %r", address, user_input[CONF_SSID])
            if (server := hub_server(self.hass)) is None:
                _LOGGER.warning("onboard %s: hub entry not loaded", address)
                errors["base"] = "hub_not_loaded"
            else:
                try:
                    await self._onboard(hub, server, address, user_input)
                except OnboardError as e:
                    errors["base"] = "onboard_failed"
                    _LOGGER.warning("onboard %s failed: %s", address, e)
                else:
                    _LOGGER.debug("onboard %s: done", address)
                    await self._reload_core_gree()
                    mac = bare_mac(address)
                    return self.async_create_entry(title=f"Gree {mac}", data=_unit_data(mac))

        return self.async_show_form(
            step_id="onboard",
            data_schema=WIFI_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._bluetooth.name or address},
        )

    async def _onboard(
        self, hub: ConfigEntry, server: GreeServer, address: str, user_input: dict[str, Any]
    ) -> dict:
        """Push the Wi-Fi credentials; meanwhile the unit may reach Gree for its first handshake."""
        mac = bare_mac(address)
        server.units.allow_upstream(mac, ONBOARD_UPSTREAM_WINDOW)
        try:
            return await async_onboard(
                self.hass,
                address,
                user_input[CONF_SSID],
                user_input[CONF_PASSWORD],
                region=hub.options.get(CONF_REGION, DEFAULT_REGION),
            )
        finally:
            server.units.end_upstream(mac)

    async def _reload_core_gree(self) -> None:
        """Let the core Gree integration discover the newly connected unit."""
        for entry in self.hass.config_entries.async_entries("gree"):
            await self.hass.config_entries.async_reload(entry.entry_id)


class GreeOfflineOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and not (errors := _hub_errors(user_input)):
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_hub_schema(
                user_input
                or {
                    CONF_ALLOWED_NETWORKS: await _default_networks(self.hass),
                    **self.config_entry.options,
                }
            ),
            errors=errors,
        )
