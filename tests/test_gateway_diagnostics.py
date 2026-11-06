"""Tests for read-only controllerless Vaillant cloud gateway diagnostics."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientResponseError
import pytest

from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.mypyllant.const import DOMAIN
from custom_components.mypyllant.gateway_diagnostics import (
    GatewayDiagnosticBinarySensor,
    GatewayDiagnosticsCoordinator,
    create_gateway_binary_sensors,
)


def _gateway(hass):
    api = SimpleNamespace(
        get_api_base=AsyncMock(return_value="https://api.example.invalid"),
        get_authorized_headers=lambda: {"Authorization": "redacted"},
    )
    sys = SimpleNamespace(
        id="system-1",
        devices=[SimpleNamespace(device_uuid="boiler-1")],
    )
    source = SimpleNamespace(
        api=api,
        data=[sys],
        _refresh_session=AsyncMock(),
    )
    return GatewayDiagnosticsCoordinator(hass, source), source


async def test_successful_diagnostics_with_empty_faults(hass):
    coordinator, source = _gateway(hass)

    def result(url):
        if url.endswith("/connection-status"):
            return {"connected": True}
        if url.endswith("/diagnostic-trouble-codes"):
            return []
        if url.endswith("/self"):
            return {
                "spineEnabled": True,
                "spineCapable": True,
                "spineEnabledStatus": "DETERMINED",
                "ski": "SENSITIVE-PRIVATE-SKI",
            }
        raise AssertionError(f"unexpected route {url}")

    coordinator._request = AsyncMock(side_effect=result)
    data = await coordinator._async_update_data()
    assert coordinator._request.call_count == 3
    assert source._refresh_session.await_count == 1
    assert data == {
        "system-1": {
            "connected": True,
            "fault_count": 0,
            "eebus_enabled": True,
            "eebus_capable": True,
            "eebus_status": "DETERMINED",
        }
    }
    assert "SENSITIVE" not in str(data)
    coordinator.async_set_updated_data(data)
    connected = GatewayDiagnosticBinarySensor(coordinator, "system-1", "boiler-1", "connected")
    faults = GatewayDiagnosticBinarySensor(coordinator, "system-1", "boiler-1", "fault_count")
    eebus = GatewayDiagnosticBinarySensor(coordinator, "system-1", "boiler-1", "eebus_enabled")
    assert connected.available and connected.is_on is True
    assert faults.available and faults.is_on is False
    assert faults.extra_state_attributes == {"reported_fault_count": 0}
    assert eebus.available and eebus.is_on is True
    assert eebus.extra_state_attributes["spine_capable"] is True
    assert eebus.extra_state_attributes["spine_enabled_status"] == "DETERMINED"
    assert eebus.device_info["identifiers"] == {(DOMAIN, "system-1_device_boiler-1")}
    assert len({x.unique_id for x in [connected, faults, eebus]}) == 3


async def test_positive_faults_and_gateway_off(hass):
    coordinator, source = _gateway(hass)
    coordinator._request = AsyncMock(side_effect=[
        {"connected": False},
        [{"codes": ["F.01"], "serialNumber": "SECRET"}, {"codes": ["F.02"]}],
        {"spineEnabled": False, "spineCapable": True, "spineEnabledStatus": "DETERMINED"},
    ])
    data = await coordinator._async_update_data()
    coordinator.async_set_updated_data(data)
    connected = GatewayDiagnosticBinarySensor(coordinator, "system-1", "boiler-1", "connected")
    faults = GatewayDiagnosticBinarySensor(coordinator, "system-1", "boiler-1", "fault_count")
    assert connected.is_on is False
    assert faults.is_on is True
    assert faults.extra_state_attributes["reported_fault_count"] == 2
    assert "SECRET" not in str(faults.extra_state_attributes)


async def test_failed_endpoint_is_unknown_not_reported_healthy(hass):
    coordinator, _ = _gateway(hass)
    coordinator._request = AsyncMock(side_effect=[
        {"connected": True},
        ClientResponseError(
            request_info=MagicMock(real_url="https://api.example.invalid"),
            history=(), status=404, message="not found",
        ),
        {"spineEnabled": True, "spineCapable": True, "spineEnabledStatus": "DETERMINED"},
    ])
    data = await coordinator._async_update_data()
    assert data["system-1"]["fault_count"] is None
    coordinator.async_set_updated_data(data)
    faults = GatewayDiagnosticBinarySensor(coordinator, "system-1", "boiler-1", "fault_count")
    assert not faults.available
    assert faults.is_on is None


async def test_quota_error_stops_subsequent_requests_and_pauses(hass):
    coordinator, _ = _gateway(hass)
    coordinator._request = AsyncMock(side_effect=ClientResponseError(
        request_info=MagicMock(real_url="https://api.example.invalid"),
        history=(), status=429, message="quota",
    ))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator._request.await_count == 1
    assert coordinator._pause_until is not None
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator._request.await_count == 1


async def test_bad_payloads_do_not_become_false_healthy_states(hass):
    coordinator, _ = _gateway(hass)
    coordinator._request = AsyncMock(side_effect=[
        {"connected": "false"},
        {"codes": []},
        {"spineEnabled": "false", "spineCapable": 1, "spineEnabledStatus": "private text"},
    ])
    data = await coordinator._async_update_data()
    coordinator.async_set_updated_data(data)
    for kind in ("connected", "fault_count", "eebus_enabled"):
        entity = GatewayDiagnosticBinarySensor(coordinator, "system-1", "boiler-1", kind)
        assert not entity.available
        assert entity.is_on is None


async def test_factory_only_registers_on_existing_boiler_device(hass):
    coordinator, source = _gateway(hass)
    hass.data[DOMAIN] = {
        "my-entry": {
            "gateway_diagnostics": coordinator,
            "system_coordinator": source,
        }
    }
    entities = create_gateway_binary_sensors(hass, "my-entry")
    assert len(entities) == 3
    assert {e._kind for e in entities} == {"connected", "fault_count", "eebus_enabled"}
    assert all(e.entity_category is not None for e in entities)
    source.data = []
    assert create_gateway_binary_sensors(hass, "my-entry") == []


def test_refresh_interval_is_conservative(hass):
    coordinator, _ = _gateway(hass)
    assert coordinator.update_interval == timedelta(hours=4)
