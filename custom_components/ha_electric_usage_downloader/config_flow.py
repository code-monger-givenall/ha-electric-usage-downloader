import logging

from homeassistant import config_entries
import voluptuous as vol

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_API_URL = "https://pec.smarthub.coop"
DEFAULT_TIMEZONE = "America/Chicago"

class ElectricUsageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Electric Usage Downloader."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Ensure username and password are not empty
            try:
                if not user_input.get("username") or not user_input.get("password"):
                    errors["base"] = "missing_credentials"
                    _LOGGER.error("Missing credentials.")
                elif not user_input.get("account_number") or not user_input.get(
                    "service_location_number"
                ):
                    errors["base"] = "missing_account_details"
                    _LOGGER.error("Missing account details.")
                else:
                    # If no errors, create the config entry
                    return self.async_create_entry(
                        title="Electric Usage Downloader", data=user_input
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
                vol.Required("account_number"): str,
                vol.Required("service_location_number"): str,
                vol.Required("timezone", default=DEFAULT_TIMEZONE): str,
                vol.Optional("extract_days", default=7): vol.All(
                    vol.Coerce(int), vol.Range(min=2, max=45)
                ),
            }),
            errors=errors,
        )
