"""Read-only reconciliation of yearly Vaillant reports with hourly Recorder data.

The annual endpoint contains DAILY values, not true historical hourly buckets.
No statistic values, IDs or Energy dashboard configuration are ever modified.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from io import StringIO
import logging
from zoneinfo import ZoneInfo

from aiohttp import ClientResponseError
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store

from .connectivity_diagnostics import device_info
from .const import CONTROLLERLESS_ENERGY_NAMES, DOMAIN

_LOGGER = logging.getLogger(__name__)

REPORT_COLUMNS = (
    "ConsumedPrimaryEnergy:Heating",
    "ConsumedPrimaryEnergy:DomesticHotWater",
    "ConsumedElectricalEnergy:Heating",
    "ConsumedElectricalEnergy:DomesticHotWater",
)
PROFILE_ORDER = tuple(CONTROLLERLESS_ENERGY_NAMES)
MAX_RANGE_DAYS = 31
MAX_CSV_SIZE = 2_000_000


def parse_annual_report(content: str, year: int) -> dict[date, tuple[Decimal, ...]]:
    """Validate the proven probe CSV format, excluding report identifiers."""
    if not isinstance(content, str) or len(content) > MAX_CSV_SIZE:
        raise ValueError("report_invalid")
    cleaned = "\n".join(
        line for line in content.lstrip("\ufeff").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    reader = csv.DictReader(StringIO(cleaned), delimiter=";")
    if reader.fieldnames != ["DateTime", *REPORT_COLUMNS]:
        raise ValueError("report_columns_changed")
    days: dict[date, tuple[Decimal, ...]] = {}
    for row in reader:
        try:
            day = date.fromisoformat(row["DateTime"].strip()[:10])
            values = tuple(Decimal(row[key].strip()) for key in REPORT_COLUMNS)
        except (KeyError, TypeError, AttributeError, ValueError, InvalidOperation) as err:
            raise ValueError("report_invalid") from err
        if (
            day.year != year
            or day in days
            or any(not value.is_finite() or value < 0 for value in values)
        ):
            raise ValueError("report_invalid")
        days[day] = values
    if not days:
        raise ValueError("report_empty")
    return days


def summarize_day(
    day: date,
    hourly_rows: list[dict],
    reported_wh: Decimal | None,
    zone: ZoneInfo,
) -> dict:
    """Classify a complete local day, including 23/25-hour DST dates."""
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    start_ts = start.astimezone(timezone.utc).timestamp()
    end_ts = end.astimezone(timezone.utc).timestamp()
    expected = round((end_ts - start_ts) / 3600)
    rows = sorted(
        (row for row in hourly_rows if start_ts <= row["start"] < end_ts),
        key=lambda row: row["start"],
    )
    result = {
        "date": day.isoformat(),
        "expected_hours": expected,
        "recorded_hours": len(rows),
        "difference_wh": None,
    }
    if reported_wh is None:
        result["status"] = "report_missing"
    elif len(rows) != expected:
        result["status"] = "hourly_missing"
    elif any(
        previous.get("sum") is None or current.get("sum") is None
        or current["sum"] < previous["sum"] - 0.01
        for previous, current in zip(rows, rows[1:])
    ):
        result["status"] = "counter_discontinuity"
    elif not rows or rows[-1].get("state") is None:
        result["status"] = "hourly_state_missing"
    else:
        # The Recorder hourly state is cumulative from local midnight; sum is
        # cumulative across days. Checking sum additionally catches reset bugs.
        first, last = rows[0], rows[-1]
        if (
            first.get("sum") is None
            or first.get("state") is None
            or last.get("sum") is None
            or abs(
                (last["sum"] - first["sum"] + first["state"])
                - last["state"]
            ) > 1
        ):
            result["status"] = "counter_discontinuity"
        else:
            diff = Decimal(str(last["state"])) - reported_wh
            result["difference_wh"] = float(diff)
            tolerance = max(Decimal("10"), reported_wh * Decimal("0.01"))
            result["status"] = (
                "verified" if abs(diff) <= tolerance else "mismatch"
            )
    return result


class HistoricalReconciliation:
    """Monthly report retrieval plus manually requested read-only comparison."""

    def __init__(self, hass: HomeAssistant, entry_id: str, system, stats_manager):
        self.hass = hass
        self.entry_id = entry_id
        self.system = system
        self.stats_manager = stats_manager
        self.store = Store(hass, 1, f"mypyllant.history_diagnostics_{entry_id}")
        self.zone = ZoneInfo(hass.config.time_zone or "Europe/Brussels")
        self.cache: dict[str, dict[str, list[str]]] = {}
        self.report_fetches: dict[str, str] = {}
        self.last_checked_month: str | None = None
        self.result: dict = {"status": "not_checked"}
        self._lock = asyncio.Lock()
        self._unsub: CALLBACK_TYPE | None = None
        self._listeners: set = set()
        self._tasks: set[asyncio.Task] = set()

    async def async_start(self) -> None:
        saved = await self.store.async_load() or {}
        self.cache = saved.get("years", {})
        self.report_fetches = saved.get("fetched", {})
        self.last_checked_month = saved.get("last_checked_month")
        self.result = saved.get("result", {"status": "not_checked"})
        self._unsub = async_track_time_change(
            self.hass, self._daily_callback, hour=3, minute=17, second=0
        )
        # Catch an installation/restart after the scheduled day, but only
        # once per month, including after restarts.
        if datetime.now(self.zone).day >= 3:
            self._schedule()

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    @callback
    def async_add_listener(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @callback
    def _daily_callback(self, now: datetime) -> None:
        if now.astimezone(self.zone).day >= 3:
            self._schedule()

    @callback
    def _schedule(self) -> None:
        task = self.hass.async_create_task(self.async_monthly_check())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _save(self) -> None:
        await self.store.async_save(
            {
                "years": self.cache,
                "fetched": self.report_fetches,
                "last_checked_month": self.last_checked_month,
                "result": self.result,
            }
        )

    def _single_system(self):
        systems = self.system.data or []
        if len(systems) != 1:
            raise ValueError("requires_one_system")
        return systems[0]

    async def _get_report(self, system, year: int, refresh: bool):
        key = str(year)
        if key in self.cache and not refresh:
            return {
                date.fromisoformat(day): tuple(Decimal(v) for v in values)
                for day, values in self.cache[key].items()
            }
        # Use the authenticated myPyllant client and existing session.
        # Never retry quota/auth failures automatically.
        await self.system._refresh_session()
        reports = [report async for report in self.system.api.get_yearly_reports(
            system, year
        )]
        if len(reports) != 1:
            raise ValueError("unexpected_report_count")
        values = parse_annual_report(reports[0].file_content, year)
        self.cache[key] = {
            day.isoformat(): [str(value) for value in row]
            for day, row in values.items()
        }
        self.report_fetches[key] = datetime.now(timezone.utc).isoformat()
        await self._save()
        return values

    async def async_reconcile(
        self,
        start: date,
        end: date,
        *,
        refresh_report: bool = False,
    ) -> dict:
        async with self._lock:
            return await self._run(start, end, refresh_report)

    async def _run(self, start: date, end: date, refresh_report: bool) -> dict:
        today = datetime.now(self.zone).date()
        if (
            start > end or end >= today
            or (end - start).days >= MAX_RANGE_DAYS
        ):
            return {"status": "invalid_range", "statistics_modified": False}
        try:
            system = self._single_system()
            ids = self.stats_manager.profile_statistics()
            if set(ids) != set(PROFILE_ORDER):
                raise ValueError("energy_profiles_not_discovered")

            daily: dict[date, tuple[Decimal, ...]] = {}
            for year in range(start.year, end.year + 1):
                # Only fetch completed years from cache; the current year is
                # refreshed monthly (or on an explicit user request).
                year_values = await self._get_report(
                    system, year, refresh_report
                )
                daily.update(year_values)

            begin = datetime.combine(start, time.min, self.zone).astimezone(
                timezone.utc
            )
            finish = datetime.combine(
                end + timedelta(days=1), time.min, self.zone
            ).astimezone(timezone.utc)
            rows = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass, begin, finish, set(ids.values()), "hour", None,
                {"sum", "state"},
            )
            mismatched: set[str] = set()
            incomplete: set[str] = set()
            verified: set[str] = set()
            total_missing_hours = 0
            largest_diff_wh = 0.0
            date_results: dict[str, dict[str, str]] = {}
            for offset in range((end - start).days + 1):
                day = start + timedelta(days=offset)
                statuses: dict[str, str] = {}
                for index, profile in enumerate(PROFILE_ORDER):
                    source = daily.get(day)
                    compared = summarize_day(
                        day, rows.get(ids[profile], []),
                        source[index] if source is not None else None,
                        self.zone,
                    )
                    statuses[CONTROLLERLESS_ENERGY_NAMES[profile]] = compared["status"]
                    if compared["status"] == "mismatch":
                        mismatched.add(day.isoformat())
                    elif compared["status"] != "verified":
                        incomplete.add(day.isoformat())
                    if compared["status"] == "hourly_missing":
                        total_missing_hours += max(
                            0,
                            compared["expected_hours"] - compared["recorded_hours"],
                        )
                    if compared["difference_wh"] is not None:
                        largest_diff_wh = max(
                            largest_diff_wh, abs(compared["difference_wh"])
                        )
                if all(value == "verified" for value in statuses.values()):
                    verified.add(day.isoformat())
                date_results[day.isoformat()] = statuses

            state = (
                "mismatch" if mismatched
                else "incomplete" if incomplete
                else "verified"
            )
            self.result = {
                "status": state,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "verified_days": len(verified),
                "mismatch_days": len(mismatched),
                "incomplete_days": len(incomplete),
                "missing_hour_rows": total_missing_hours,
                "largest_difference_kwh": round(largest_diff_wh / 1000, 3),
                "last_verified_day": max(verified) if verified else None,
                "checked_report_years": list(range(start.year, end.year + 1)),
                "daily_statuses": date_results,
                "statistics_modified": False,
            }
        except ClientResponseError as err:
            status = getattr(err, "status", 0)
            reason = (
                "rate_limited" if status == 429
                else "authentication" if status == 401
                else "forbidden_or_quota" if status == 403
                else "report_request_failed"
            )
            self.result = {"status": "error", "reason": reason}
        except (TimeoutError, OSError):
            self.result = {"status": "error", "reason": "api_unavailable"}
        except ValueError as err:
            # Only known sanitized validation codes, never full API bodies.
            reason = str(err)
            allowed = {
                "requires_one_system", "energy_profiles_not_discovered",
                "unexpected_report_count", "report_invalid",
                "report_columns_changed", "report_empty",
            }
            self.result = {
                "status": "error",
                "reason": reason if reason in allowed else "report_invalid",
            }
        await self._save()
        self._notify()
        return self.result

    async def async_monthly_check(self) -> None:
        now = datetime.now(self.zone)
        if now.day < 3:
            return
        period = now.strftime("%Y-%m")
        async with self._lock:
            if self.last_checked_month == period:
                return
            # Persist the attempt BEFORE making a cloud request so that
            # repeated HA restarts never hammer a rate-limited endpoint.
            self.last_checked_month = period
            await self._save()
            first_this_month = date(now.year, now.month, 1)
            end = first_this_month - timedelta(days=1)
            start = date(end.year, end.month, 1)
            await self._run(start, end, refresh_report=True)


class HistoricalQualitySensor(SensorEntity):
    """Read-only diagnostic for the most recent historical comparison."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(
        self, system_id: str, device_uuid: str,
        diagnostics: HistoricalReconciliation,
    ) -> None:
        self.diagnostics = diagnostics
        self._attr_name = "Vaillant Historical Energy Quality"
        self._attr_unique_id = (
            f"{DOMAIN}_{system_id}_{device_uuid}_historical_energy_quality"
        )
        self._attr_device_info = device_info(system_id, device_uuid)

    @property
    def native_value(self) -> str:
        return self.diagnostics.result.get("status", "not_checked")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            key: value for key, value in self.diagnostics.result.items()
            if key not in {"status", "statistics_modified"}
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.diagnostics.async_add_listener(self.async_write_ha_state)
        )


def create_history_sensors(
    hass: HomeAssistant, entry_id: str
) -> list[HistoricalQualitySensor]:
    entry = hass.data[DOMAIN][entry_id]
    diagnostics: HistoricalReconciliation = entry["historical_diagnostics"]
    return [
        HistoricalQualitySensor(system.id, system.devices[0].device_uuid, diagnostics)
        for system in entry["system_coordinator"].data or []
        if system.devices
    ]
