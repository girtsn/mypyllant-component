"""Regression tests for controllerless hourly-only Vaillant statistics."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import UnitOfEnergy
from myPyllant.models import DeviceDataBucket

from custom_components.mypyllant.const import DOMAIN
from custom_components.mypyllant.hourly_statistics import (
    HourlyStatisticsManager,
    _StatisticsOnlyPublisher,
)
from custom_components.mypyllant.sensor import (
    ControllerlessTodayEnergySensor,
    create_controllerless_energy_sensors,
)
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
    assert metadata["name"] == "Electricity Heating"
    assert len(records) == 3
    assert records[-1]["sum"] == 720.0
    await manager.async_stop()


def test_controllerless_options_only_expose_polling():
    from custom_components.mypyllant.config_flow import OPTIONS_SCHEMA

    option_keys = {marker.schema for marker in OPTIONS_SCHEMA.schema}
    assert option_keys == {"update_interval", "update_interval_daily"}


async def test_device_energy_today_is_read_only_and_uses_same_boiler(hass):
    """Friendly device sensors expose consumption without duplicate statistics."""
    midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    source = _make_sensor(
        hass,
        [
            DeviceDataBucket(
                start_date=midnight,
                end_date=midnight + timedelta(hours=1),
                value=1200.0,
            )
        ],
    )
    mirror = ControllerlessTodayEnergySensor(source.system_id, 0, 0, source.coordinator)
    mirror.hass = hass
    assert mirror.native_value == 1.2
    assert mirror.name == "Electricity Heating Today"
    assert mirror.unique_id == f"{source.unique_id}_today"
    assert mirror.device_info == source.device_info
    assert mirror.state_class is None
    assert mirror.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR

    config = SimpleNamespace(entry_id="test-entry")
    hass.data[DOMAIN] = {"test-entry": {"daily_data_coordinator": source.coordinator}}
    sensors = create_controllerless_energy_sensors(hass, config)
    assert len(sensors) == 1
    assert sensors[0].unique_id == mirror.unique_id

    with (
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as writer,
    ):
        await mirror.async_added_to_hass()
        mirror._handle_coordinator_update = MagicMock()
        writer.assert_not_called()
    await mirror.async_will_remove_from_hass()


def test_cleanup_removes_orphaned_entities_but_keeps_supported_sensors(hass):
    """Remove controller-only entities for this entry, not Recorder history."""
    coordinator = MagicMock()
    manager = HourlyStatisticsManager(hass, coordinator, "test-entry")
    entries = [
        SimpleNamespace(entity_id="sensor.keep", domain="sensor", unique_id="keep"),
        SimpleNamespace(entity_id="sensor.old_daily", domain="sensor", unique_id="old"),
        SimpleNamespace(entity_id="switch.away", domain="switch", unique_id="away"),
        SimpleNamespace(
            entity_id="binary_sensor.old", domain="binary_sensor", unique_id="oldb"
        ),
    ]
    with (
        patch("custom_components.mypyllant.hourly_statistics.er.async_get") as get_reg,
        patch(
            "custom_components.mypyllant.hourly_statistics.er.async_entries_for_config_entry",
            return_value=entries,
        ),
    ):
        assert manager.remove_obsolete_entities({"keep"}) == 3
        assert {
            call.args[0] for call in get_reg.return_value.async_remove.call_args_list
        } == {"sensor.old_daily", "switch.away", "binary_sensor.old"}
        get_reg.return_value.async_remove.reset_mock()
        assert manager.remove_obsolete_entities(set()) == 0
        get_reg.return_value.async_remove.assert_not_called()


async def test_first_publish_refreshes_existing_metadata_without_changing_sum(hass):
    """Ensure old long Recorder names update even with no new hourly buckets."""
    buckets = _make_buckets([100.0, 200.0, 300.0])
    source = _make_sensor(hass, buckets)
    publisher = _StatisticsOnlyPublisher(source.system_id, 0, 0, source.coordinator)
    publisher.hass = hass
    publisher._refresh_metadata_if_unchanged = True
    stat_id = f"{DOMAIN}:{source.unique_id}".lower().replace("-", "_")

    async def current_rows(func, hass, start, *args):
        if start < buckets[0].start_date:
            return {}
        return {
            stat_id: [
                {"start": buckets[0].start_date.timestamp(), "sum": 100.0},
                {"start": buckets[1].start_date.timestamp(), "sum": 300.0},
                {"start": buckets[2].start_date.timestamp(), "sum": 600.0},
            ]
        }

    with (
        patch("custom_components.mypyllant.sensor.get_instance") as rec,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as write,
    ):
        rec.return_value.async_add_executor_job = AsyncMock(side_effect=current_rows)
        await publisher._write_hourly_statistics()

    write.assert_called_once()
    _, metadata, records = write.call_args.args
    assert metadata["name"] == "Electricity Heating"
    assert metadata["statistic_id"] == stat_id
    assert len(records) == 1
    assert records[0]["sum"] == 600.0
    assert records[0]["state"] == 600.0
