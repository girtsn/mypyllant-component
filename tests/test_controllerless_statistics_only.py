"""Regression tests for controllerless hourly-only Vaillant statistics."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import UnitOfEnergy
from myPyllant.models import DeviceDataBucket

from custom_components.mypyllant.const import DOMAIN, energy_profile_type
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
    """Create four visible energy entities even when hourly startup data is empty."""
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
    profiles = [
        ("CONSUMED_PRIMARY_ENERGY", "HEATING"),
        ("CONSUMED_PRIMARY_ENERGY", "DOMESTIC_HOT_WATER"),
        ("CONSUMED_ELECTRICAL_ENERGY", "HEATING"),
        ("CONSUMED_ELECTRICAL_ENERGY", "DOMESTIC_HOT_WATER"),
    ]
    device = SimpleNamespace(
        device_uuid="test-uuid",
        name_display="ecoTEC",
        brand_name="Vaillant",
        product_name_display="ecoTEC",
        data=[
            SimpleNamespace(energy_type=None, value_type=kind, operation_mode=mode)
            for kind, mode in profiles
        ],
    )
    systems = SimpleNamespace(
        data=[
            SimpleNamespace(
                id=source.system_id,
                devices=[device],
                home=SimpleNamespace(home_name="Test Home", nomenclature="Test Home"),
            )
        ]
    )
    config = SimpleNamespace(entry_id="test-entry")
    saved_data = source.device_data
    source.coordinator.data = {}
    hass.data[DOMAIN] = {
        "test-entry": {
            "system_coordinator": systems,
            "daily_data_coordinator": source.coordinator,
        }
    }
    sensors = create_controllerless_energy_sensors(hass, config)
    assert len(sensors) == 4
    assert all(
        isinstance(sensor, ControllerlessTodayEnergySensor) for sensor in sensors
    )
    assert all(sensor.native_value is None for sensor in sensors)
    assert len({sensor.unique_id for sensor in sensors}) == 4
    assert all(
        sensor.device_info["identifiers"]
        == {(DOMAIN, f"{source.system_id}_device_test-uuid")}
        for sensor in sensors
    )
    assert all(sensor.state_class is None for sensor in sensors)
    assert all(
        sensor.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
        for sensor in sensors
    )

    # Refresh becomes available later: no restart is needed to create entities.
    source.coordinator.data = {
        source.system_id: {
            "home_name": "Test Home",
            "devices_data": [[saved_data]],
        }
    }
    electric = next(
        sensor for sensor in sensors if sensor.name == "Electricity Heating Today"
    )
    assert electric.native_value == 1.2
    assert electric.unique_id.endswith("_consumed_electrical_energy_heating_today")

    electric.hass = hass
    with (
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as writer,
    ):
        await electric.async_added_to_hass()
        writer.assert_not_called()
    await electric.async_will_remove_from_hass()


async def test_no_data_does_not_exhaust_metadata_rename(hass):
    """The first empty Vaillant response must not skip Recorder name migration."""
    coordinator = MagicMock()
    coordinator.data = {}
    manager = HourlyStatisticsManager(hass, coordinator, "test-entry")
    await manager.async_publish()
    assert manager._refresh_metadata_pending is True


def test_cleanup_removes_orphaned_entities_but_keeps_supported_sensors(hass):
    """Only known legacy IDs and controller-only domains are removed."""
    coordinator = MagicMock()
    manager = HourlyStatisticsManager(hass, coordinator, "test-entry")
    entries = [
        SimpleNamespace(entity_id="sensor.keep", domain="sensor", unique_id="keep"),
        SimpleNamespace(
            entity_id="sensor.temp_unavailable",
            domain="sensor",
            unique_id="mypyllant_optional_diagnostic",
        ),
        SimpleNamespace(
            entity_id="sensor.old_daily",
            domain="sensor",
            unique_id="mypyllant_test_system_test-uuid_2_0",
        ),
        SimpleNamespace(
            entity_id="sensor.old_efficiency",
            domain="sensor",
            unique_id="mypyllant_test_system_heating_energy_efficiency",
        ),
        SimpleNamespace(entity_id="switch.away", domain="switch", unique_id="away"),
        SimpleNamespace(
            entity_id="binary_sensor.old",
            domain="binary_sensor",
            unique_id="oldb",
        ),
    ]
    with (
        patch("custom_components.mypyllant.hourly_statistics.er.async_get") as get_reg,
        patch(
            "custom_components.mypyllant.hourly_statistics.er.async_entries_for_config_entry",
            return_value=entries,
        ),
    ):
        assert manager.remove_obsolete_entities({"keep"}) == 4
        assert {
            call.args[0] for call in get_reg.return_value.async_remove.call_args_list
        } == {
            "sensor.old_daily",
            "sensor.old_efficiency",
            "switch.away",
            "binary_sensor.old",
        }
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


def test_real_gateway_uses_value_type_not_energy_type():
    """myPyllant 0.9.18 preserves currentSystem.valueType separately."""
    from myPyllant.models import DeviceData

    energy = DeviceData(
        operation_mode="HEATING",
        value_type="CONSUMED_PRIMARY_ENERGY",
    )
    assert energy.energy_type is None
    assert energy_profile_type(energy) == "CONSUMED_PRIMARY_ENERGY"


async def test_publisher_recognizes_current_system_value_type_only(hass):
    """Statistics must be published with original IDs when energyType is absent."""
    source = _make_sensor(hass, _make_buckets([120.0, 240.0]))
    profile = source.device_data
    profile.value_type = profile.energy_type
    profile.energy_type = None
    manager = HourlyStatisticsManager(hass, source.coordinator, "test-entry")
    with (
        patch("custom_components.mypyllant.sensor.get_instance") as recorder,
        patch(
            "custom_components.mypyllant.sensor.async_add_external_statistics"
        ) as writer,
    ):
        recorder.return_value.async_add_executor_job = AsyncMock(return_value={})
        await manager.async_start()
    writer.assert_called_once()
    _, metadata, stats = writer.call_args.args
    assert metadata["statistic_id"] == (
        f"{DOMAIN}:{source.unique_id}".lower().replace("-", "_")
    )
    assert metadata["name"] == "Electricity Heating"
    assert len(stats) == 2
    await manager.async_stop()


async def test_hourly_poll_uses_current_system_value_type(
    hass, daily_data_coordinator_mock
):
    """Do not skip all EMF profile buckets when the declaration has valueType."""
    from myPyllant.models import DeviceData

    source = _make_sensor(hass, _make_buckets([1200.0]))
    definition = DeviceData(
        operation_mode="HEATING",
        value_type="CONSUMED_ELECTRICAL_ENERGY",
    )
    device = source.device
    device.data = [definition]
    system = SimpleNamespace(
        id=source.system_id,
        timezone=timezone.utc,
        home=SimpleNamespace(home_name="Test Home", nomenclature="Test Home"),
        devices=[device],
    )
    daily_data_coordinator_mock.hass_data["system_coordinator"].data = [system]

    fetched = DeviceData(
        operation_mode="HEATING",
        data=_make_buckets([1200.0]),
        total_consumption=1200.0,
    )

    async def get_buckets(device_arg, resolution, start, end):
        assert device_arg is device
        assert definition.skip_data_update is False
        yield fetched

    daily_data_coordinator_mock.api.get_data_by_device = get_buckets
    with patch.object(
        daily_data_coordinator_mock,
        "_refresh_session",
        new_callable=AsyncMock,
    ):
        result = await daily_data_coordinator_mock._async_update_data()

    assert result[source.system_id]["devices_data"][0][0].data
    assert fetched.energy_type == "CONSUMED_ELECTRICAL_ENERGY"
    assert fetched.device is device
