"""Regression tests for controllerless hourly-only Vaillant statistics."""

from unittest.mock import AsyncMock, patch

from custom_components.mypyllant.const import DOMAIN
from custom_components.mypyllant.hourly_statistics import HourlyStatisticsManager
from tests.test_sensor import _make_buckets, _make_sensor


async def test_manager_preserves_statistic_id_and_shortens_name(hass):
    """Removing the daily sensors must not change the Energy dashboard source."""
    old = _make_sensor(hass, _make_buckets([120.0, 240.0, 360.0]))
    manager = HourlyStatisticsManager(hass, old.coordinator, "test-entry")
    with (
        patch("custom_components.mypyllant.sensor.get_instance") as recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as write,
    ):
        recorder.return_value.async_add_executor_job = AsyncMock(return_value={})
        await manager.async_start()

    write.assert_called_once()
    _, metadata, records = write.call_args.args
    assert metadata["statistic_id"] == (
        f"{DOMAIN}:{old.unique_id}".lower().replace("-", "_")
    )
    assert metadata["name"] == "Vaillant Electricity Heating"
    assert len(records) == 3
    assert records[-1]["sum"] == 720.0
    await manager.async_stop()


def test_controllerless_options_only_expose_polling():
    from custom_components.mypyllant.config_flow import OPTIONS_SCHEMA

    option_keys = {marker.schema for marker in OPTIONS_SCHEMA.schema}
    assert option_keys == {"update_interval", "update_interval_daily"}
