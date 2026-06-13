import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the sensor platform from a config entry."""
    try:
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        async_add_entities([ElectricUsageSensor(coordinator)])
    except KeyError as e:
        _LOGGER.error(f"Error setting up sensor entry: {e}")

class ElectricUsageSensor(CoordinatorEntity, RestoreEntity, SensorEntity):
    """Representation of an electric usage sensor."""

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Electric Usage"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_unique_id = "electric_usage"
        self._attr_native_value = None
        self._last_interval_end = None
        self._last_interval_kwh = None
        self._record_count = None

    async def async_added_to_hass(self):
        """Restore state before processing coordinator data."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state not in (None, "unknown", "unavailable"):
            try:
                self._attr_native_value = float(state.state)
            except ValueError:
                self._attr_native_value = None

            self._last_interval_end = state.attributes.get("last_interval_end")

        self._process_coordinator_data()

    @property
    def extra_state_attributes(self):
        """Return diagnostic attributes for the latest SmartHub fetch."""
        return {
            "last_interval_end": self._last_interval_end,
            "last_interval_kwh": self._last_interval_kwh,
            "fetched_record_count": self._record_count,
        }

    @property
    def available(self):
        """Return True if the sensor is available."""
        return self.coordinator.last_update_success

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._process_coordinator_data()
        self.async_write_ha_state()

    def _process_coordinator_data(self):
        """Fold newly fetched intervals into a monotonically increasing total."""
        data = self.coordinator.data or {}
        records = data.get("records") or []
        self._record_count = data.get("record_count")
        if not records:
            return

        records = sorted(records, key=lambda record: record["end"])
        latest_record = records[-1]

        if self._attr_native_value is None:
            self._attr_native_value = round(sum(record["kwh"] for record in records), 3)
            self._last_interval_end = latest_record["end"]
            self._last_interval_kwh = latest_record["kwh"]
            return

        if not self._last_interval_end:
            self._last_interval_end = latest_record["end"]
            self._last_interval_kwh = latest_record["kwh"]
            return

        last_interval_end = dt_util.parse_datetime(self._last_interval_end)
        if last_interval_end is None:
            return

        new_records = [
            record
            for record in records
            if (dt_util.parse_datetime(record["end"]) or last_interval_end) > last_interval_end
        ]
        if not new_records:
            self._last_interval_kwh = latest_record["kwh"]
            return

        self._attr_native_value = round(
            float(self._attr_native_value) + sum(record["kwh"] for record in new_records),
            3,
        )
        self._last_interval_end = new_records[-1]["end"]
        self._last_interval_kwh = new_records[-1]["kwh"]
