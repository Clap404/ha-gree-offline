from homeassistant.helpers.device_registry import format_mac

DOMAIN = "gree_offline"
SIGNAL_STATS = f"{DOMAIN}_stats"

# Hub options
CONF_ADVERTISED_IP = "advertised_ip"
CONF_REGION = "region"
CONF_RESOLVER = "resolver"
CONF_SINK_DELAY = "sink_delay"

# Onboarding form
CONF_PASSWORD = "password"
CONF_SSID = "ssid"

# Entry data
CONF_KIND = "kind"
CONF_MAC = "mac"
CONF_IP = "ip"
CONF_LAST_CLOUD_HANDSHAKE = "last_cloud_handshake"

# Config entries are either the single server hub or one per AC unit.
KIND_HUB = "hub"
KIND_UNIT = "unit"

DEFAULT_REGION = "eu"
# Regions with a live <region>.dis.gree.com:1813 (verified 2026-10-16); others via free text.
REGIONS = ["eu", "hk", "in", "ru", "sa", "au"]
# External DNS, used only when the system DNS points Gree names at the local redirect.
DEFAULT_RESOLVERS = "1.1.1.1, 8.8.8.8"
DEFAULT_SINK_DELAY = 0.5

DIS_PORT = 1813
DIS_PLAIN_PORT = 5000
GATE_PORT = 1992
MQTT_PORT = 1992

# Max seconds a unit may reach Gree while the Bluetooth flow onboards it.
ONBOARD_UPSTREAM_WINDOW = 180.0
GATE_WINDOW = 15.0
GATE_HOLD = 300.0
# A unit counts as connected if it reached the server within this many seconds.
ACTIVE_WINDOW = 120.0

GENERIC_KEY = b"a3K8Bx%2r8Y7#xDh"


def dis_host(region: str) -> str:
    return f"{region}.dis.gree.com"


def mqtt_host(region: str) -> str:
    return f"mqtt-{region}.gree.com"


def bare_mac(address: str) -> str:
    """Lowercase hex without separators, the form the unit itself uses."""
    return address.replace(":", "").lower()


def unit_unique_id(mac: str) -> str:
    """Unit entries are keyed by the BLE address, which equals the Wi-Fi MAC on these units."""
    return format_mac(mac).upper()
