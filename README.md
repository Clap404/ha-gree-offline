# Gree Offline

![Baseball bat with a "* Bonk *" caption hitting the blue and orange Gree logo](custom_components/gree_offline/brand/icon.png)

Home Assistant integration that onboards Gree air conditioners (tested with Wi-Fi module firmware 1.45) over Bluetooth and keeps them off Gree's cloud. Control stays with Home Assistant's core `gree` integration.

## Features

- Connect Gree AC units to your Wi-Fi network over Bluetooth, **without a Gree account**.
- **Offline operation** after the initial handshake with Gree's servers during onboarding.

## How it works

Gree units with firmware >= 1.31 only stay connected to a Wi-Fi network if they can reach Gree's servers through it.

To achieve offline operation, the integration answers the unit's discovery requests itself. While a unit is being onboarded over Bluetooth, it relays the unit's first handshake to Gree's servers (encrypted traffic), just enough to satisfy the unit, then cuts the cloud connection. The unit never reaches Gree again.

The unit then stays connected to Wi-Fi, but keeps retrying Gree's MQTT server several times per second, and ignores local commands while a retry is in progress. The integration accepts each retry, holds it briefly (see _Sink delay_ below), then closes it. This slows the retries down enough for the unit to answer Home Assistant in between.

## Setup

[![Open HACS repository on My Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Clap404&repository=ha-gree-offline&category=integration)

1. Install through HACS (custom repository), or deploy manually by copying `custom_components/gree_offline` into your Home Assistant install.
2. Restart Home Assistant, then add the **Gree Offline** integration and pick your units' region.
3. Redirect Gree's DNS entries to your Home Assistant server (see below).
4. Units in pairing mode (press MODE + WIFI on the remote until it beeps) show up in Home Assistant's **Devices & services** screen. Confirm them there.
5. Units already connected to your Wi-Fi network only look up the discovery server when they start. Power-cycle each one once the DNS redirect is in place and it shows up the same way.
6. Once a unit is connected to your Wi-Fi network, Home Assistant's Gree integration picks it up normally.

Home Assistant must use host networking (the default for Home Assistant OS, Supervised and the documented
container setup), and TCP ports 1813, 5000 and 1992 must be free.

### Options

- **Advertised IP**: the address units are told to connect to. Leave blank to use Home Assistant's own IP, detected automatically. Set it if Home Assistant has several addresses or sits behind a NAT/proxy and units cannot reach the detected one. Ideally it is the address your DNS redirect points to.
- **Allowed networks** (required): comma-separated CIDRs your units connect from, e.g. `192.168.67.0/24` for an IoT VLAN. Pre-filled with Home Assistant's own subnet. Connections from anywhere else are closed right away, so Home Assistant must see the units' real addresses (no NAT in between).
- **Sink delay** (default 0.5 s): how long each of a unit's MQTT retries is held before being closed. Longer means fewer retries, but the unit does not answer local commands while held, so its replies to Home Assistant get slower. 0 closes immediately.

## DNS redirect

DNS redirection is optional, but without it, units onboarded here will use Gree's cloud normally. To keep them local, redirect DNS queries for Gree's servers to your Home Assistant server.

| name                   | purpose                                  |
| ---------------------- | ---------------------------------------- |
| `REGION.dis.gree.com`  | discovery; also the unit's fallback name |
| `mqtt-REGION.gree.com` | MQTT broker                              |

Regions with a live discovery server: `eu`, `hk`, `in`, `ru`, `sa`, `au`.

Example using OpenWrt / dnsmasq, for region `eu` and Home Assistant at `192.168.1.10`:

```
uci add_list dhcp.@dnsmasq[0].address='/eu.dis.gree.com/192.168.1.10'
uci add_list dhcp.@dnsmasq[0].address='/mqtt-eu.gree.com/192.168.1.10'
uci commit dhcp && /etc/init.d/dnsmasq restart
```

Example using plain dnsmasq: `address=/eu.dis.gree.com/192.168.1.10` and `address=/mqtt-eu.gree.com/192.168.1.10`.

To reach Gree's real servers (only while onboarding), the integration queries an external DNS server directly instead of your local one: `1.1.1.1, 8.8.8.8` by default, configurable in the integration options. Outbound DNS queries to those servers must not be blocked.

Each unit's **Connected** sensor shows whether it is using the server; if it stays off, the redirect is missing or the units are not using it.

## Credits and disclaimer

This integration builds on the work of:

- [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge): the Bluetooth onboarding flow is based on its Gree implementation.
- [gree-dummy-tls-server](https://codeberg.org/joserebelo/gree-dummy-tls-server) by joserebelo: fake discovery server for older firmware, and [issue #3](https://codeberg.org/joserebelo/gree-dummy-tls-server/issues/3) that documented the MQTT behaviour of firmware 1.31+.
- [greeaclocalserver](https://github.com/SilentLeader/greeaclocalserver) by SilentLeader: local server emulation of the discovery protocol.
- [gree-hvac-protocol](https://github.com/bekmansurov/gree-hvac-protocol) by bekmansurov: protocol documentation.

This project was mostly vibe-coded, but human reviewed and tested.

## License

[AGPL-3.0](LICENSE.md).
