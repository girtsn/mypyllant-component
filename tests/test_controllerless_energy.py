"""Regression tests for controller-free EMF energy discovery."""

from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mypyllant.controllerless_api import (
    ControllerlessMyPyllantAPI,
)
from myPyllant.api import MyPyllantAPI


def _fake_api(controller, current_system):
    """Build a fake authenticated API without making any network requests."""
    api = object.__new__(ControllerlessMyPyllantAPI)
    api.brand = "vaillant"
    api.control_identifiers = {"system-1": controller}
    api.get_control_identifier = AsyncMock(side_effect=ValueError(controller))
    api.get_api_base = AsyncMock(return_value="https://api.example.invalid")

    home = SimpleNamespace(system_id="system-1", timezone=timezone.utc)
    api.get_homes = Mock(return_value=_homes(home))

    response = AsyncMock()
    response.raise_for_status = Mock()
    response.json = AsyncMock(return_value=current_system)
    api.aiohttp_session = Mock()
    api.aiohttp_session.get.return_value = AsyncMock()
    api.aiohttp_session.get.return_value.__aenter__ = AsyncMock(
        return_value=response
    )
    api.aiohttp_session.get.return_value.__aexit__ = AsyncMock(
        return_value=False
    )
    return api, home


async def _homes(home):
    yield home


@pytest.mark.asyncio
async def test_controller_none_discovers_energy_devices():
    """The controller is absent but currentSystem contains usable energy data."""
    api, home = _fake_api(
        "none",
        {
            "primaryHeatGenerator": {
                "deviceUuid": "boiler-1",
                "productName": "ecoTEC",
                "data": [
                    {
                        "operationMode": "HEATING",
                        "valueType": "CONSUMED_PRIMARY_ENERGY",
                    }
                ],
            },
            "secondaryHeatGenerators": None,
        },
    )
    expected_system = object()
    with patch(
        "custom_components.mypyllant.controllerless_api.System.from_api",
        return_value=expected_system,
    ) as make_system:
        systems = [system async for system in api.get_systems(homes=[home])]

    assert systems == [expected_system]
    api.aiohttp_session.get.assert_called_once()
    assert "/emf/v2/system-1/currentSystem" in (
        api.aiohttp_session.get.call_args.args[0]
    )
    kwargs = make_system.call_args.kwargs
    assert kwargs["state"] == {}
    assert kwargs["configuration"] == {}
    assert kwargs["current_system"]["primary_heat_generator"][
        "device_uuid"
    ] == "boiler-1"


@pytest.mark.asyncio
async def test_controller_none_without_energy_data_is_skipped():
    """Do not synthesize a boiler if the EMF response has no energy profiles."""
    api, home = _fake_api("none", {"primaryHeatGenerator": None})
    with patch(
        "custom_components.mypyllant.controllerless_api.System.from_api"
    ) as make_system:
        systems = [system async for system in api.get_systems(homes=[home])]

    assert systems == []
    make_system.assert_not_called()


@pytest.mark.asyncio
async def test_supported_controller_still_uses_upstream_systems():
    """Controllers supported by myPyllant retain the existing code path."""
    api, home = _fake_api("tli", {})
    api.get_control_identifier = AsyncMock(
        return_value=SimpleNamespace(is_unsupported=False)
    )
    expected = object()

    async def upstream(self, **kwargs):
        assert kwargs["homes"] == [home]
        yield expected

    with patch.object(MyPyllantAPI, "get_systems", upstream):
        systems = [system async for system in api.get_systems(homes=[home])]

    assert systems == [expected]
    api.aiohttp_session.get.assert_not_called()
