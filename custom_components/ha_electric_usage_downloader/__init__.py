import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, SCAN_INTERVAL
from .api import ElectricUsageAPI

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]
DEFAULT_API_URL = "https://pec.smarthub.coop"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Electric Usage Downloader from a config entry."""
    username = entry.data["username"]
    password = entry.data["password"]
    api_url = entry.data.get("api_url") or _origin_from_url(
        entry.data.get("login_url"), DEFAULT_API_URL
    )
    account_number = entry.data.get("account_number") or entry.data.get("account")
    service_location_number = entry.data.get("service_location_number") or entry.data.get(
        "service_location"
    )
    usage_timezone = entry.data.get("timezone") or hass.config.time_zone
    extract_days = int(entry.data.get("extract_days", 7))

    session = async_get_clientsession(hass)
    api = ElectricUsageAPI(
        session,
        username,
        password,
        api_url,
        account_number,
        service_location_number,
        usage_timezone,
        extract_days,
    )

    coordinator = ElectricUsageCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()
    if (
        api.account_number
        and api.service_location_number
        and (
            entry.data.get("account_number") != api.account_number
            or entry.data.get("service_location_number") != api.service_location_number
        )
    ):
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "account_number": api.account_number,
                "service_location_number": api.service_location_number,
            },
            version=2,
        )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entries."""
    if entry.version == 1:
        data = {**entry.data}
        if "api_url" not in data:
            data["api_url"] = _origin_from_url(data.get("login_url"), DEFAULT_API_URL)
        if "timezone" not in data:
            data["timezone"] = hass.config.time_zone
        if "extract_days" not in data:
            data["extract_days"] = 7
        hass.config_entries.async_update_entry(entry, data=data, version=2)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the Electric Usage Downloader."""
    try:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if unload_ok:
            hass.data[DOMAIN].pop(entry.entry_id)
        return unload_ok
    except Exception:
        _LOGGER.exception("Error unloading entry")
        return False

class ElectricUsageCoordinator(DataUpdateCoordinator):
    """Coordinator to manage fetching electric usage data."""

    def __init__(self, hass: HomeAssistant, api: ElectricUsageAPI):
        """Initialize the coordinator."""
        self.api = api
        super().__init__(
            hass,
            _LOGGER,
            name="Electric Usage Coordinator",
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self):
        """Fetch data from the API."""
        return await self.api.get_usage_data()


def _origin_from_url(url: str | None, default: str) -> str:
    """Return the origin from a URL string."""
    if not url:
        return default
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return default
    return f"{parsed.scheme}://{parsed.netloc}"
