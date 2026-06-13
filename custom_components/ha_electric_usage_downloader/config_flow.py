import logging

from homeassistant import config_entries
from homeassistant.core import callback
import voluptuous as vol

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_API_URL = "https://pec.smarthub.coop"
DEFAULT_TIMEZONE = "America/Chicago"

class ElectricUsageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Electric Usage Downloader."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return ElectricUsageOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Ensure username and password are not empty
            try:
                data = {
                    key: value.strip() if isinstance(value, str) else value
                    for key, value in user_input.items()
                    if value not in (None, "")
                }
                if not data.get("username") or not data.get("password"):
                    errors["base"] = "missing_credentials"
                    _LOGGER.error("Missing credentials.")
                else:
                    # If no errors, create the config entry
                    return self.async_create_entry(
                        title="Electric Usage Downloader", data=data
                    )
            except Exception as e:
                _LOGGER.error(f"Error during config flow: {e}")
                errors["base"] = "unknown_error"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("username"): str,
                vol.Required("password"): str,
                vol.Required("api_url", default=DEFAULT_API_URL): str,
                vol.Optional("account_number"): str,
                vol.Optional("service_location_number"): str,
                vol.Required("timezone", default=DEFAULT_TIMEZONE): str,
                vol.Optional("extract_days", default=7): vol.All(
                    vol.Coerce(int), vol.Range(min=2, max=45)
                ),
            }),
            errors=errors,
        )


class ElectricUsageOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Electric Usage Downloader."""

    def __init__(self, config_entry):
        """Initialize the options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage integration options."""
        if user_input is not None:
            data = {
                key: value.strip() if isinstance(value, str) else value
                for key, value in user_input.items()
                if value not in (None, "")
            }
            return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    "account_number",
                    default=self._option_or_data("account_number", ""),
                ): str,
                vol.Optional(
                    "service_location_number",
                    default=self._option_or_data("service_location_number", ""),
                ): str,
                vol.Optional(
                    "api_url",
                    default=self._option_or_data("api_url", DEFAULT_API_URL),
                ): str,
                vol.Optional(
                    "timezone",
                    default=self._option_or_data("timezone", DEFAULT_TIMEZONE),
                ): str,
                vol.Optional(
                    "extract_days",
                    default=self._option_or_data("extract_days", 7),
                ): vol.All(vol.Coerce(int), vol.Range(min=2, max=45)),
            }),
        )

    def _option_or_data(self, key, default):
        """Return an option value, falling back to config entry data."""
        return self.config_entry.options.get(
            key, self.config_entry.data.get(key, default)
        )
