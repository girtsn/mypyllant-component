"""Regression tests for Vaillant cloud and historical report diagnostics."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from aiohttp import ClientResponseError

from custom_components.mypyllant.connectivity_diagnostics import (
    CloudConnectionEntity,
    CloudDiagnosticSensor,
    CloudHealth,
)
from custom_components.mypyllant.const import DOMAIN
from custom_components.mypyllant.history_diagnostics import (
    HistoricalQualitySensor,
    HistoricalReconciliation,
    PROFILE_ORDER,
    REPORT_COLUMNS,
    parse_annual_report,
    summarize_day,
)

ZONE = ZoneInfo("Europe/Brussels")


def report_csv(day: date, values=(24, 48, 72, 96)) -> str:
    return (
        "DateTime;" + ";".join(REPORT_COLUMNS) + "\n"
        + day.isoformat() + ";" + ";".join(map(str, values)) + "\n"
    )


def hourly_rows(day: date, hourly_wh: int) -> list[dict]:
    start = datetime.combine(day, time.min, ZONE).astimezone(timezone.utc)
    finish = datetime.combine(
        day + timedelta(days=1), time.min, ZONE
    ).astimezone(timezone.utc)
    expected = round((finish - start).total_seconds() / 3600)
    return [
        {
            "start": (start + timedelta(hours=index)).timestamp(),
            "state": (index + 1) * hourly_wh,
            "sum": (index + 1) * hourly_wh,
        }
        for index in range(expected)
    ]


def test_validate_report_and_never_accept_missing_dates_as_zero():
    day = date(2025, 6, 24)
    assert parse_annual_report(report_csv(day), 2025)[day] == (
        Decimal(24), Decimal(48), Decimal(72), Decimal(96)
    )
    with pytest.raises(ValueError):
        parse_annual_report(report_csv(day) + report_csv(day).splitlines()[1] + "\n", 2025)
    with pytest.raises(ValueError):
        parse_annual_report(report_csv(day), 2024)
    with pytest.raises(ValueError):
        parse_annual_report(report_csv(day).replace(";24;", ";-24;"), 2025)
    with pytest.raises(ValueError):
        parse_annual_report("DateTime;Unexpected\n2025-01-01;100", 2025)


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25),
     (date(2026, 11, 1), 24)],
)
def test_reconciliation_uses_correct_dst_day_length(day, hours):
    rows = hourly_rows(day, 10)
    result = summarize_day(day, rows, Decimal(hours * 10), ZONE)
    assert result["expected_hours"] == hours
    assert result["recorded_hours"] == hours
    assert result["status"] == "verified"


def test_zero_hourly_readings_do_not_look_like_valid_consumption():
    day = date(2026, 10, 25)
    rows = hourly_rows(day, 0)
    result = summarize_day(day, rows, Decimal(1200), ZONE)
    assert result["status"] == "mismatch"
    assert result["difference_wh"] == -1200
    assert summarize_day(day, rows[:-1], Decimal(1200), ZONE)["status"] == "hourly_missing"
    assert summarize_day(day, rows, None, ZONE)["status"] == "report_missing"


def test_cloud_connectivity_uses_real_on_off_and_stays_available(hass):
    system = MagicMock()
    system.last_update_success = True
    system._quota_exc_info = None
    energy = MagicMock()
    energy.last_update_success = False
    energy.data = {}
    health = CloudHealth(hass, system, energy)
    health.start()
    connected = CloudConnectionEntity("system-1", "device-1", health)
    assert connected.is_on is True
    assert connected.available is True
    assert connected.unique_id.endswith("_cloud_connection_state")
    assert connected.device_info["identifiers"] == {
        (DOMAIN, "system-1_device_device-1")
    }
    system.last_update_success = False
    health._system_refreshed()
    assert connected.available is True
    assert connected.is_on is False
    assert connected.extra_state_attributes["failure_reason"] == "api_unavailable"
    assert health.consecutive_failures == 1
    system.last_update_success = True
    health._system_refreshed()
    assert connected.is_on is True
    assert health.consecutive_failures == 0
    assert CloudDiagnosticSensor(
        "system-1", "device-1", health, "last_api_contact"
    ).native_value is not None
    health.stop()


async def test_yearly_report_reconciliation_uses_original_ids_and_cache(hass):
    day = date(2025, 6, 24)
    reports = MagicMock()
    reports.get_yearly_reports = MagicMock()

    async def report_generator(system, year):
        assert year == 2025
        yield SimpleNamespace(file_content=report_csv(day))

    reports.get_yearly_reports.side_effect = report_generator
    system = SimpleNamespace(
        data=[SimpleNamespace(id="system1")],
        api=reports,
        _refresh_session=AsyncMock(),
    )
    ids = {profile: f"mypyllant:real-stable-{index}" for index, profile in enumerate(PROFILE_ORDER)}
    manager = SimpleNamespace(profile_statistics=lambda: ids)
    diagnostics = HistoricalReconciliation(hass, "entry1", system, manager)
    diagnostics.store = SimpleNamespace(async_save=AsyncMock())
    rows = {
        stat_id: hourly_rows(day, (index + 1))
        for index, stat_id in enumerate(ids.values())
    }
    with patch(
        "custom_components.mypyllant.history_diagnostics.get_instance"
    ) as recorder:
        recorder.return_value.async_add_executor_job = AsyncMock(return_value=rows)
        first = await diagnostics.async_reconcile(day, day, refresh_report=True)
        second = await diagnostics.async_reconcile(day, day)

    assert first["status"] == "verified"
    assert second["status"] == "verified"
    assert first["verified_days"] == 1
    assert first["mismatch_days"] == 0
    assert first["statistics_modified"] is False
    assert reports.get_yearly_reports.call_count == 1
    entity = HistoricalQualitySensor("system1", "device1", diagnostics)
    assert entity.native_value == "verified"
    assert entity.device_info["identifiers"] == {
        (DOMAIN, "system1_device_device1")
    }


async def test_monthly_reconciliation_never_refetches_after_restart_marker(hass):
    system = SimpleNamespace(data=[])
    diagnostics = HistoricalReconciliation(
        hass, "entry1", system, SimpleNamespace()
    )
    diagnostics.store = SimpleNamespace(async_save=AsyncMock())
    diagnostics.last_checked_month = datetime.now(ZONE).strftime("%Y-%m")
    with patch.object(diagnostics, "_run", new_callable=AsyncMock) as run:
        await diagnostics.async_monthly_check()
    run.assert_not_called()


async def test_unavailable_report_does_not_modify_recorder(hass):
    system = SimpleNamespace(data=[object()])
    manager = SimpleNamespace(profile_statistics=lambda: {})
    diagnostics = HistoricalReconciliation(hass, "entry1", system, manager)
    diagnostics.store = SimpleNamespace(async_save=AsyncMock())
    result = await diagnostics.async_reconcile(
        date(2025, 6, 1), date(2025, 6, 2)
    )
    assert result["status"] == "error"
    assert result["reason"] == "energy_profiles_not_discovered"
    assert not result.get("statistics_modified", False)
