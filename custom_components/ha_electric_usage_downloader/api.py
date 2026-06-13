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
        account_number: str | None,
        service_location_number: str | None,
        usage_timezone: str,
        extract_days: int,
    ):
        """Initialize the API client."""
        self.session = session
        self.username = username
        self.password = password
        self.api_url = api_url.rstrip("/")
        self.account_number = self._string_from_value(account_number)
        self.service_location_number = self._string_from_value(service_location_number)
        self.usage_timezone = usage_timezone
        self.extract_days = extract_days
        self._authorization_token: str | None = None
        self._smarthub_user_id: str | None = None
        self._customer_number: str | None = None

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
        self._smarthub_user_id = (
            self._first_string_for_keys(
                data,
                ("userId", "username", "userName", "email", "registeredEmail"),
                max_depth=2,
            )
            or self.username
        )
        self._customer_number = self._first_string_for_keys(
            data, ("customerNumber", "customer", "customerId", "custNbr"), max_depth=3
        )

    async def get_usage_data(self):
        """Fetch recent electric usage interval data from SmartHub."""
        if not self._authorization_token:
            await self.login()

        now = datetime.now(ZoneInfo(self.usage_timezone))
        start = now - timedelta(days=max(2, min(self.extract_days, 45)))
        for attempt in range(10):
            try:
                await self._ensure_usage_identifiers()
                payload = await self._poll_usage(start, now)
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

    async def _ensure_usage_identifiers(self) -> None:
        """Discover SmartHub account and service location if the user omitted them."""
        if self.account_number and self.service_location_number:
            return

        payload = await self._get_user_data()
        candidates = self._find_usage_candidates(payload)
        if not candidates:
            accounts_payload = None
            accounts_error = None
            try:
                accounts_payload = await self._get_accounts_data()
                candidates = self._find_usage_candidates(accounts_payload)
            except ElectricUsageAPIError as err:
                accounts_error = str(err)

        if not candidates:
            _LOGGER.warning(
                (
                    "Could not discover SmartHub account/service location. "
                    "User-data shape: %s; accounts shape: %s; accounts error: %s"
                ),
                self._payload_shape(payload),
                self._payload_shape(accounts_payload),
                accounts_error,
            )
            raise ElectricUsageAPIError(
                "Could not discover SmartHub account/service location from user data"
            )

        candidate = self._best_usage_candidate(candidates)
        self.account_number = candidate["account_number"]
        self.service_location_number = candidate["service_location_number"]
        _LOGGER.info("Discovered SmartHub usage account and service location")

    async def _get_user_data(self) -> Any:
        """Fetch SmartHub account metadata used by the portal account picker."""
        last_payload: Any = None
        for params in self._user_data_param_sets():
            payload = await self._request_user_data(params)
            last_payload = payload
            if self._payload_has_content(payload):
                return payload

        return last_payload

    async def _request_user_data(self, params: dict[str, str]) -> Any:
        """Fetch SmartHub account metadata with one parameter set."""
        user_data_url = f"{self.api_url}/services/secured/user-data"
        headers = {
            **self._base_headers(),
            "authorization": f"Bearer {self._authorization_token}",
            "x-nisc-smarthub-username": self.username,
        }

        async with self.session.get(
            user_data_url, params=params, headers=headers
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise ElectricUsageAPIError(
                    f"SmartHub user-data failed with HTTP {response.status}: {body[:200]}"
                )
            try:
                return await response.json(content_type=None)
            except Exception as err:
                raise ElectricUsageAPIError(
                    f"SmartHub user-data returned non-JSON response: {body[:200]}"
                ) from err

    def _user_data_param_sets(self) -> list[dict[str, str]]:
        """Return SmartHub user-data parameter combinations to try."""
        param_sets: list[dict[str, str]] = []
        user_ids = [self._smarthub_user_id, self.username]
        for user_id in dict.fromkeys(user_id for user_id in user_ids if user_id):
            if self._customer_number:
                param_sets.append({"userId": user_id, "customer": self._customer_number})
            param_sets.append({"userId": user_id})

        if self._customer_number:
            param_sets.append({"customer": self._customer_number})
        param_sets.append({})
        return param_sets

    async def _get_accounts_data(self) -> Any:
        """Fetch SmartHub account data from the account picker endpoint."""
        last_payload: Any = None
        for params in self._accounts_param_sets():
            payload = await self._request_accounts_data(params)
            last_payload = payload
            if self._payload_has_content(payload):
                return payload

        return last_payload

    async def _request_accounts_data(self, params: dict[str, str]) -> Any:
        """Fetch SmartHub account data with one parameter set."""
        accounts_url = f"{self.api_url}/services/secured/accounts"
        headers = {
            **self._base_headers(),
            "authorization": f"Bearer {self._authorization_token}",
            "content-type": "application/json",
            "x-nisc-smarthub-username": self.username,
        }

        async with self.session.get(accounts_url, params=params, headers=headers) as response:
            body = await response.text()
            if response.status >= 400:
                raise ElectricUsageAPIError(
                    f"SmartHub accounts failed with HTTP {response.status}: {body[:200]}"
                )
            try:
                return await response.json(content_type=None)
            except Exception as err:
                raise ElectricUsageAPIError(
                    f"SmartHub accounts returned non-JSON response: {body[:200]}"
                ) from err

    def _accounts_param_sets(self) -> list[dict[str, str]]:
        """Return SmartHub accounts endpoint parameter combinations to try."""
        param_sets: list[dict[str, str]] = []
        user_ids = [self._smarthub_user_id, self.username]
        for user_id in dict.fromkeys(user_id for user_id in user_ids if user_id):
            if self._customer_number:
                param_sets.append({"user": user_id, "customer": self._customer_number})
            param_sets.append({"user": user_id})

        if self._customer_number:
            param_sets.append({"customer": self._customer_number})
        param_sets.append({})
        return param_sets

    async def _poll_usage(self, start: datetime, end: datetime) -> dict[str, Any]:
        """Call SmartHub's usage polling endpoint."""
        if not self.account_number or not self.service_location_number:
            raise ElectricUsageAPIError("SmartHub account/service location is missing")

        usage_url = f"{self.api_url}/services/secured/utility-usage/poll"
        payload = {
            "timeFrame": "HOURLY",
            "userId": self.username,
            "screen": "USAGE_EXPLORER",
            "includeDemand": False,
            "serviceLocationNumber": self.service_location_number,
            "accountNumber": self.account_number,
            "industries": ["ELECTRIC"],
            "startDateTime": int(start.timestamp() * 1000),
            "endDateTime": int(end.timestamp() * 1000),
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

    def _find_usage_candidates(self, payload: Any) -> list[dict[str, Any]]:
        """Return account/service-location pairs from SmartHub user-data."""
        candidates: list[dict[str, Any]] = []
        for user_data in self._iter_user_data_records(payload):
            account = self._first_string_for_keys(
                user_data, ("account", "accountNumber", "acctNbr", "accountId")
            )
            if not account:
                continue

            candidates.extend(self._candidates_from_user_data_record(user_data, account))

        candidates.extend(self._generic_usage_candidates(payload))
        return self._dedupe_candidates(candidates)

    def _iter_user_data_records(self, payload: Any):
        """Yield SmartHub user-data account records from common response shapes."""
        if isinstance(payload, dict):
            user_data = payload.get("userData")
            if isinstance(user_data, list):
                for record in user_data:
                    if isinstance(record, dict):
                        yield record
            elif isinstance(user_data, dict):
                yield user_data

            for value in payload.values():
                if isinstance(value, (dict, list)):
                    yield from self._iter_user_data_records(value)
        elif isinstance(payload, list):
            for value in payload:
                if isinstance(value, dict):
                    if self._looks_like_user_data_record(value):
                        yield value
                    yield from self._iter_user_data_records(value)

    def _looks_like_user_data_record(self, value: dict[str, Any]) -> bool:
        """Return True when a dict resembles a SmartHub user-data account."""
        return bool(
            self._first_string_for_keys(
                value, ("account", "accountNumber", "acctNbr", "accountId"), max_depth=2
            )
            and (
                any(
                    key in value
                    for key in (
                        "primaryServiceLocationId",
                        "serviceLocationIdToServiceLocationSummary",
                        "serviceLocationToIndustries",
                        "serviceLocationToProviders",
                        "serviceLocationToUserDataServiceLocationSummaries",
                    )
                )
                or self._first_string_for_keys(
                    value,
                    (
                        "serviceLocation",
                        "serviceLocationNumber",
                        "serviceLocationId",
                        "srvLoc",
                        "srvLocNbr",
                    ),
                    max_depth=2,
                )
            )
        )

    def _candidates_from_user_data_record(
        self, user_data: dict[str, Any], account: str
    ) -> list[dict[str, Any]]:
        """Extract service locations from a single SmartHub account record."""
        candidates: list[dict[str, Any]] = []
        industries_by_location = user_data.get("serviceLocationToIndustries")
        summaries_by_location = user_data.get(
            "serviceLocationToUserDataServiceLocationSummaries"
        )

        for service_location, industries in self._iter_mapping_entries(
            industries_by_location
        ):
            service_location = self._string_from_value(service_location)
            if not service_location:
                service_location = self._first_string_for_keys(
                    industries,
                    (
                        "serviceLocation",
                        "serviceLocationNumber",
                        "serviceLocationId",
                        "srvLoc",
                        "srvLocNbr",
                    ),
                    max_depth=3,
                )
            if service_location:
                candidates.append(
                    {
                        "account_number": account,
                        "service_location_number": service_location,
                        "electric": self._contains_electric(industries),
                        "active": True,
                        "source": "serviceLocationToIndustries",
                    }
                )

        for service_location, summaries in self._iter_mapping_entries(
            summaries_by_location
        ):
            service_location = self._string_from_value(service_location)
            if not service_location:
                service_location = self._first_string_for_keys(
                    summaries,
                    (
                        "serviceLocation",
                        "serviceLocationNumber",
                        "serviceLocationId",
                        "srvLoc",
                        "srvLocNbr",
                    ),
                    max_depth=4,
                )
            if not service_location:
                continue
            candidates.append(
                {
                    "account_number": account,
                    "service_location_number": service_location,
                    "electric": self._contains_electric(summaries),
                    "active": self._contains_active_service(summaries),
                    "source": "serviceLocationToUserDataServiceLocationSummaries",
                }
            )

        direct_service_location = self._string_from_value(
            user_data.get("serviceLocation")
            or user_data.get("serviceLocationNumber")
            or user_data.get("serviceLocationId")
            or user_data.get("srvLoc")
            or user_data.get("srvLocNbr")
            or self._service_location_from_id(user_data.get("primaryServiceLocationId"))
        )
        if not direct_service_location:
            direct_service_location = self._first_string_for_keys(
                user_data,
                (
                    "serviceLocation",
                    "serviceLocationNumber",
                    "serviceLocationId",
                    "srvLoc",
                    "srvLocNbr",
                ),
                max_depth=3,
            )
        if direct_service_location:
            candidates.append(
                {
                    "account_number": account,
                    "service_location_number": direct_service_location,
                    "electric": self._contains_electric(user_data),
                    "active": not user_data.get("inactive", False),
                    "source": "direct",
                }
            )

        summaries = user_data.get("serviceLocationIdToServiceLocationSummary")
        for service_location, summary in self._iter_mapping_entries(summaries):
            service_location = self._string_from_value(
                self._service_location_from_id(
                    summary.get("id") if isinstance(summary, dict) else None
                )
                or service_location
            )
            if not service_location:
                service_location = self._first_string_for_keys(
                    summary,
                    (
                        "serviceLocation",
                        "serviceLocationNumber",
                        "serviceLocationId",
                        "srvLoc",
                        "srvLocNbr",
                    ),
                    max_depth=3,
                )
            if service_location:
                candidates.append(
                    {
                        "account_number": account,
                        "service_location_number": service_location,
                        "electric": self._contains_electric(
                            self._mapping_value_for_key(
                                industries_by_location, service_location
                            )
                            or summary
                        ),
                        "active": not user_data.get("inactive", False),
                        "source": "serviceLocationIdToServiceLocationSummary",
                    }
                )

        return candidates

    def _generic_usage_candidates(self, payload: Any) -> list[dict[str, Any]]:
        """Fallback extraction for less common SmartHub JSON shapes."""
        candidates: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            account = self._first_string_for_keys(
                payload, ("account", "accountNumber", "acctNbr", "accountId"), max_depth=2
            )
            service_location = self._first_string_for_keys(
                payload,
                (
                    "serviceLocation",
                    "serviceLocationNumber",
                    "serviceLocationId",
                    "srvLoc",
                    "srvLocNbr",
                ),
                max_depth=2,
            )
            if account and service_location:
                candidates.append(
                    {
                        "account_number": account,
                        "service_location_number": service_location,
                        "electric": self._contains_electric(payload),
                        "active": not payload.get("inactive", False),
                        "source": "generic",
                    }
                )

            for value in payload.values():
                candidates.extend(self._generic_usage_candidates(value))
        elif isinstance(payload, list):
            for value in payload:
                candidates.extend(self._generic_usage_candidates(value))

        return candidates

    def _best_usage_candidate(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Choose the most likely electric service location."""
        candidates = [
            candidate
            for candidate in candidates
            if (
                not self.account_number
                or candidate["account_number"] == self.account_number
            )
            and (
                not self.service_location_number
                or candidate["service_location_number"] == self.service_location_number
            )
        ] or candidates

        return max(candidates, key=self._candidate_score)

    def _candidate_score(self, candidate: dict[str, Any]) -> tuple[int, int, int]:
        """Score discovered candidates without logging account details."""
        return (
            1 if candidate.get("electric") else 0,
            1 if candidate.get("active") else 0,
            1 if candidate.get("source") == "serviceLocationToIndustries" else 0,
        )

    def _dedupe_candidates(
        self, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge duplicate account/service-location candidates."""
        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in candidates:
            account = candidate.get("account_number")
            service_location = candidate.get("service_location_number")
            if not account or not service_location:
                continue

            key = (account, service_location)
            existing = deduped.get(key)
            if existing:
                existing["electric"] = existing.get("electric") or candidate.get("electric")
                existing["active"] = existing.get("active") or candidate.get("active")
            else:
                deduped[key] = candidate

        return list(deduped.values())

    def _service_location_from_id(self, value: Any) -> str | None:
        """Extract a SmartHub service location string from an id object."""
        if isinstance(value, dict):
            direct = self._string_from_value(
                value.get("serviceLocation")
                or value.get("serviceLocationNumber")
                or value.get("serviceLocationId")
                or value.get("srvLoc")
                or value.get("srvLocNbr")
            )
            if direct:
                return direct
            return self._first_string_for_keys(
                value,
                (
                    "serviceLocation",
                    "serviceLocationNumber",
                    "serviceLocationId",
                    "srvLoc",
                    "srvLocNbr",
                ),
                max_depth=3,
            )
        return self._string_from_value(value)

    def _iter_mapping_entries(self, value: Any):
        """Yield key/value pairs from SmartHub map encodings."""
        if isinstance(value, dict):
            entries = value.get("entries")
            if isinstance(entries, list):
                for entry in entries:
                    key, child = self._entry_key_value(entry)
                    yield key, child
                return

            for key, child in value.items():
                yield key, child
            return

        if isinstance(value, list):
            for entry in value:
                key, child = self._entry_key_value(entry)
                yield key, child

    def _entry_key_value(self, entry: Any) -> tuple[Any, Any]:
        """Return a best-effort key/value pair from a serialized map entry."""
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            return entry[0], entry[1]
        if isinstance(entry, dict):
            key = (
                entry.get("key")
                or entry.get("name")
                or entry.get("id")
                or entry.get("serviceLocation")
                or entry.get("serviceLocationNumber")
                or entry.get("srvLocNbr")
            )
            child = (
                entry.get("value")
                or entry.get("values")
                or entry.get("items")
                or entry.get("data")
                or entry
            )
            return key, child
        return None, entry

    def _mapping_value_for_key(self, mapping: Any, lookup_key: str) -> Any:
        """Return a map value from any SmartHub map encoding."""
        for key, value in self._iter_mapping_entries(mapping):
            if self._string_from_value(key) == lookup_key:
                return value
        return None

    def _contains_active_service(self, value: Any) -> bool:
        """Return True when summaries include an active electric service."""
        statuses: list[str] = []

        def collect_statuses(item: Any) -> None:
            if isinstance(item, dict):
                status = item.get("serviceStatus")
                if status:
                    statuses.append(str(status).upper())
                for child in item.values():
                    collect_statuses(child)
            elif isinstance(item, list):
                for child in item:
                    collect_statuses(child)

        collect_statuses(value)
        return not statuses or any(
            status in {"ACTIVE", "PENDING_DISCONNECT"} for status in statuses
        )

    def _contains_electric(self, value: Any) -> bool:
        """Return True if nested SmartHub data references electric service."""
        if isinstance(value, dict):
            return any(self._contains_electric(child) for child in value.values())
        if isinstance(value, list):
            return any(self._contains_electric(child) for child in value)
        if value is None:
            return False
        text = str(value).upper()
        return "ELECTRIC" in text or text in {"EL", "E"}

    def _string_from_value(self, value: Any) -> str | None:
        """Convert common SmartHub scalar values into clean strings."""
        if value is None:
            return None
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        text = str(value).strip()
        return text or None

    def _first_string_for_keys(
        self, value: Any, keys: tuple[str, ...], max_depth: int = 4
    ) -> str | None:
        """Find the first scalar string for any key within a bounded depth."""
        if max_depth < 0:
            return None
        if isinstance(value, dict):
            for key in keys:
                candidate = value.get(key)
                if not isinstance(candidate, (dict, list)):
                    text = self._string_from_value(candidate)
                    if text:
                        return text
            for child in value.values():
                text = self._first_string_for_keys(child, keys, max_depth - 1)
                if text:
                    return text
        elif isinstance(value, list):
            for child in value:
                text = self._first_string_for_keys(child, keys, max_depth - 1)
                if text:
                    return text
        return None

    def _payload_shape(self, payload: Any, max_depth: int = 3) -> Any:
        """Return a sanitized key-only shape summary for debugging."""
        if max_depth < 0:
            return type(payload).__name__
        if isinstance(payload, dict):
            shape: dict[str, Any] = {}
            for index, (key, value) in enumerate(payload.items()):
                if index >= 20:
                    shape["..."] = f"{len(payload) - 20} more keys"
                    break
                shape[key] = self._payload_shape(value, max_depth - 1)
            return shape
        if isinstance(payload, list):
            first = payload[0] if payload else None
            return {
                "type": "list",
                "len": len(payload),
                "first": self._payload_shape(first, max_depth - 1),
            }
        return type(payload).__name__

    def _payload_has_content(self, payload: Any) -> bool:
        """Return True when a SmartHub response has non-empty user data."""
        if payload is None:
            return False
        if isinstance(payload, list):
            return len(payload) > 0
        if isinstance(payload, dict):
            return any(self._payload_has_content(value) for value in payload.values())
        if isinstance(payload, str):
            return bool(payload.strip())
        return True

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
