# HA Electric Usage Downloader

The **HA Electric Usage Downloader** integration allows you to download and display your electric usage data from the PEC SmartHub portal directly in Home Assistant. This integration polls the SmartHub API every 15 minutes to provide interval data about your electricity consumption.

## Features
- **Electric Usage Data**: Automatically fetches your electric usage from the PEC SmartHub API every 15 minutes.
- **Energy Dashboard Metadata**: Exposes a kWh sensor with energy device class and total-increasing state class.
- **Configurable SmartHub API Details**: Allows you to configure the SmartHub API URL and timezone, with optional account number and service location overrides.

## Requirements
- PEC SmartHub account credentials (username and password).
- PEC SmartHub account number and service location number are discovered automatically when SmartHub exposes them through user data. If discovery fails, enter them manually.
- Home Assistant (version 2023.1.0 or higher).

---

## Installation Instructions

### Installation via HACS

To install this integration via HACS (Home Assistant Community Store), follow these steps:

1. Open **Home Assistant** and go to **HACS** > **Integrations**.
2. Click on the three-dot menu in the upper-right corner and select **Custom Repositories**.
3. In the repository URL field, add the following: https://github.com/smue86/ha-electric-usage-downloader
4. Set the **Category** to **Integration** and click **Add**.
5. After adding the repository, search for `HA Electric Usage Downloader` in the HACS Integrations tab.
6. Click **Install**.
7. Restart Home Assistant to apply the changes.

### Manual Installation

If you prefer to install the integration manually:

1. Download the latest version of the integration from the GitHub repository: https://github.com/smue86/ha-electric-usage-downloader
2. Copy the `ha_electric_usage_downloader` folder from `custom_components/` into your Home Assistant `custom_components/` directory.
3. Restart Home Assistant.

---

## Configuration Instructions

After installation, you can configure the integration through the Home Assistant UI.

1. Go to **Settings** > **Devices & Services** > **Add Integration**.
2. Search for `HA Electric Usage Downloader` and select it.
3. Enter your **username** and **password** for the PEC SmartHub portal.
4. Input the **API URL** and **timezone** for your SmartHub provider. Leave account number and service location number blank unless automatic discovery fails or you want to force a specific meter/location.
- Default PEC values:
  - API URL: `https://pec.smarthub.coop`
  - Timezone: `America/Chicago`
5. Complete the configuration, and a new sensor entity will be created with your electric usage data.

---

## Usage

Once the integration is configured, you will have a sensor in Home Assistant that displays cumulative electric usage in kWh. This data will be updated every 15 minutes.

You can view this sensor in your Home Assistant dashboard or use it in automations, scripts, or notifications to track your energy consumption.

---

## Troubleshooting

If you encounter issues:
- **Verify API details**: Ensure that you have entered the correct API URL, account number, service location number, and timezone for your provider.
- **Check Logs**: Look at the Home Assistant logs (under **Settings** > **System** > **Logs**) for any error messages related to the integration.
- **Authentication Errors**: If login fails, ensure your credentials are correct for the PEC SmartHub portal.

---

## Dependencies

This integration uses Home Assistant's built-in HTTP client and does not install additional Python requirements.

---

## FAQ

**1. What if my SmartHub provider uses a different URL?**

You can enter the correct API URL for your provider during setup. The integration is flexible and works with SmartHub instances that support the same `services/oauth/auth/v2` and `services/secured/utility-usage/poll` API endpoints.

**2. How often does the integration fetch data?**

The integration fetches data every 15 minutes by default, but you can adjust this interval in the integration settings if needed.

---

## Support

For any issues or feature requests, please create an issue on the [GitHub repository](https://github.com/smue86/ha-electric-usage-downloader).
