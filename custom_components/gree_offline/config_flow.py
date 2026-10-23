"""Config flow: a single server hub, plus per-unit onboarding from Bluetooth discovery."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode
import voluptuous as vol

from .ble import OnboardError, async_onboard
from .const import (
    CONF_ADVERTISED_IP,
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
            vol.Optional(CONF_RESOLVER, default=current.get(CONF_RESOLVER, DEFAULT_RESOLVERS)): str,
            vol.Optional(
                CONF_SINK_DELAY, default=current.get(CONF_SINK_DELAY, DEFAULT_SINK_DELAY)
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
        }
    )


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
        if user_input is not None:
            return self.async_create_entry(
                title="Gree Offline server", data={CONF_KIND: KIND_HUB}, options=user_input
            )
        return self.async_show_form(step_id="user", data_schema=_hub_schema({}))

    # Unit already on Wi-Fi

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """A unit already on Wi-Fi contacted the server."""
        self._seen_mac, self._seen_ip = discovery_info[CONF_MAC], discovery_info[CONF_IP]
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
        await self.async_set_unique_id(unit_unique_id(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._bluetooth = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name or discovery_info.address}
        return await self.async_step_onboard()

    async def async_step_onboard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._bluetooth is not None
        if (hub := hub_entry(self.hass)) is None:
            return self.async_abort(reason="no_hub")
        address = self._bluetooth.address

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await self._onboard(hub, address, user_input)
            except OnboardError as e:
                errors["base"] = "onboard_failed"
                _LOGGER.warning("onboard %s failed: %s", address, e)
            else:
                await self._reload_core_gree()
                mac = info.get("mac") or bare_mac(address)
                return self.async_create_entry(title=f"Gree {mac}", data=_unit_data(mac))

        return self.async_show_form(
            step_id="onboard",
            data_schema=WIFI_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._bluetooth.name or address},
        )

    async def _onboard(self, hub: ConfigEntry, address: str, user_input: dict[str, Any]) -> dict:
        """Push the Wi-Fi credentials; meanwhile the unit may reach Gree for its first handshake."""
        server = hub_server(self.hass)
        mac = bare_mac(address)
        if server:
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
            if server:
                server.units.end_upstream(mac)

    async def _reload_core_gree(self) -> None:
        """Let the core Gree integration discover the newly connected unit."""
        for entry in self.hass.config_entries.async_entries("gree"):
            await self.hass.config_entries.async_reload(entry.entry_id)


class GreeOfflineOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_hub_schema(dict(self.config_entry.options))
        )
