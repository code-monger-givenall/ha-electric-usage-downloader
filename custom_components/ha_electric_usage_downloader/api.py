import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp

_LOGGER = logging.getLogger(__name__)


class ElectricUsageAPIError(Exception):
    """Base exception for SmartHub API errors."""


class ElectricUsagePendingError(ElectricUsageAPIError):
    """Raised when SmartHub is still preparing usage data."""


class ElectricUsageAPI:
    """Handles communication with the PEC SmartHub portal."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        api_url: str,
        account_number: str,
        service_location_number: str,
        usage_timezone: str,
        extract_days: int,
    ):
        """Initialize the API client."""
        self.session = session
        self.username = username
        self.password = password
        self.api_url = api_url.rstrip("/")
        self.account_number = account_number
        self.service_location_number = service_location_number
        self.usage_timezone = usage_timezone
        self.extract_days = extract_days
        self._authorization_token: str | None = None

    async def login(self):
        """Log in to SmartHub and retrieve an authorization token."""
        auth_url = f"{self.api_url}/services/oauth/auth/v2"
        payload = {"userId": self.username, "password": self.password}
        headers = self._base_headers()

        async with self.session.post(auth_url, data=payload, headers=headers) as response:
            body = await response.text()
            if response.status >= 400:
                raise ElectricUsageAPIError(
                    f"SmartHub auth failed with HTTP {response.status}: {body[:200]}"
                )

            try:
                data = await response.json(content_type=None)
            except Exception as err:
                raise ElectricUsageAPIError(
                    f"SmartHub auth returned non-JSON response: {body[:200]}"
                ) from err

        token = data.get("authorizationToken")
        if not token:
            raise ElectricUsageAPIError("SmartHub auth response did not include a token")

        self._authorization_token = token

    async def get_usage_data(self):
        """Fetch recent electric usage interval data from SmartHub."""
        if not self._authorization_token:
            await self.login()

        for attempt in range(10):
            try:
                payload = await self._poll_usage()
                records = self._parse_usage_records(payload)
                return {
                    "records": records,
                    "record_count": len(records),
                    "usage": round(sum(record["kwh"] for record in records), 3),
                }
            except ElectricUsagePendingError:
                if attempt == 9:
                    raise
                await asyncio.sleep(1)
            except ElectricUsageAPIError as err:
                if "HTTP 401" not in str(err):
                    raise
                self._authorization_token = None
                await self.login()

        raise ElectricUsageAPIError("SmartHub usage data was not ready")

    async def _poll_usage(self) -> dict[str, Any]:
        """Call SmartHub's usage polling endpoint."""
        usage_url = f"{self.api_url}/services/secured/utility-usage/poll"
        now = datetime.now(ZoneInfo(self.usage_timezone))
        start = now - timedelta(days=max(2, min(self.extract_days, 45)))
        payload = {
            "timeFrame": "HOURLY",
            "userId": self.username,
            "screen": "USAGE_EXPLORER",
            "includeDemand": False,
            "serviceLocationNumber": self.service_location_number,
            "accountNumber": self.account_number,
            "industries": ["ELECTRIC"],
            "startDateTime": int(start.timestamp() * 1000),
            "endDateTime": int(now.timestamp() * 1000),
        }
        headers = {
            **self._base_headers(),
            "authorization": f"Bearer {self._authorization_token}",
            "content-type": "application/json",
            "x-nisc-smarthub-username": self.username,
        }

        async with self.session.post(usage_url, json=payload, headers=headers) as response:
            body = await response.text()
            if response.status >= 400:
                raise ElectricUsageAPIError(
                    f"SmartHub usage poll failed with HTTP {response.status}: {body[:200]}"
                )
            try:
                return await response.json(content_type=None)
            except Exception as err:
                raise ElectricUsageAPIError(
                    f"SmartHub usage poll returned non-JSON response: {body[:200]}"
                ) from err

    def _parse_usage_records(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse SmartHub poll response into interval records."""
        if payload.get("status") != "COMPLETE":
            raise ElectricUsagePendingError("SmartHub usage data is still pending")

        electric_data = payload.get("data", {}).get("ELECTRIC")
        if not electric_data:
            raise ElectricUsageAPIError("SmartHub usage response did not include ELECTRIC data")

        usage_series = []
        for data in electric_data:
            if data.get("type") == "USAGE":
                usage_series.extend(data.get("series") or [])

        if not usage_series:
            raise ElectricUsageAPIError("SmartHub usage response did not include USAGE series")

        records: list[dict[str, Any]] = []
        for series in usage_series:
            points = series.get("data") or []
            if not points:
                continue

            period = self._period_for_points(points)
            meter_name = series.get("name")
            for point in points:
                if "x" not in point or "y" not in point:
                    continue

                start = self._smart_hub_timestamp_to_datetime(point["x"])
                end = start + period
                records.append(
                    {
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "kwh": float(point["y"]),
                        "meter_name": meter_name,
                    }
                )

        if not records:
            raise ElectricUsageAPIError("SmartHub usage response contained no interval records")

        return sorted(records, key=lambda record: record["end"])

    def _period_for_points(self, points: list[dict[str, Any]]) -> timedelta:
        """Infer the interval duration represented by usage points."""
        if len(points) < 2:
            return timedelta(hours=1)
        try:
            return timedelta(milliseconds=int(points[1]["x"]) - int(points[0]["x"]))
        except (KeyError, TypeError, ValueError):
            return timedelta(hours=1)

    def _smart_hub_timestamp_to_datetime(self, timestamp_ms: int | float) -> datetime:
        """Convert SmartHub's local-as-UTC timestamp into a real zoned datetime."""
        usage_tz = ZoneInfo(self.usage_timezone)
        fake_utc = datetime.fromtimestamp(float(timestamp_ms) / 1000, timezone.utc)
        local_time = fake_utc.replace(tzinfo=None)
        return local_time.replace(tzinfo=usage_tz)

    def _base_headers(self) -> dict[str, str]:
        """Return headers common to SmartHub API requests."""
        parsed = urlparse(self.api_url)
        return {
            "accept": "application/json, text/plain, */*",
            "authority": parsed.hostname or "",
            "user-agent": "Mozilla/5.0",
        }
