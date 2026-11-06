"""Cloud access diagnostics for controllerless Vaillant installations.

These entities observe existing coordinators. They NEVER poll the cloud.
A connectivity entity stays available while its source coordinator fails.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientResponseError
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN


class CloudHealth:
    """Last successful operations, consecutive failures and cloud state."""

    def __init__(
        self,
        hass: HomeAssistant,
        system: DataUpdateCoordinator,
        energy: DataUpdateCoordinator,
    ) -> None:
        self.hass = hass
        self.system = system
        self.energy = energy
        self.last_api_contact: datetime | None = None
        self.last_energy_contact: datetime | None = None
        self.latest_energy_bucket: datetime | None = None
        self.consecutive_failures = 0
        self._listeners: set[Callable[[], None]] = set()
        self._unsubscribers: list[Callable[[], None]] = []

    def start(self) -> None:
        self._unsubscribers = [
            self.system.async_add_listener(self._system_refreshed),
            self.energy.async_add_listener(self._energy_refreshed),
        ]
        self._system_refreshed()
        self._energy_refreshed()

    def stop(self) -> None:
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        self._listeners.clear()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @property
    def cloud_connected(self) -> bool:
        return bool(self.system.last_update_success)

    @property
    def failure_reason(self) -> str:
        if self.cloud_connected:
            return "none"
        exc = getattr(self.system, "_quota_exc_info", None)
        if isinstance(exc, ClientResponseError):
            if exc.status == 429:
                return "rate_limited"
            if exc.status == 401:
                return "authentication"
            if exc.status == 403:
                return "forbidden_or_quota"
            return "http_error"
        if isinstance(exc, (TimeoutError,)):
            return "timeout"
        return "api_unavailable"

    @callback
    def _system_refreshed(self) -> None:
        if self.system.last_update_success:
            self.last_api_contact = datetime.now(timezone.utc)
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
        self._notify()

    @callback
    def _energy_refreshed(self) -> None:
        if self.energy.last_update_success:
            self.last_energy_contact = datetime.now(timezone.utc)
            latest = self.latest_energy_bucket
            for system in (self.energy.data or {}).values():
                for device_data in system.get("devices_data", []):
                    for profile in device_data:
                        for bucket in profile.data or []:
                            if bucket.value is None:
                                continue
                            if latest is None or bucket.start_date > latest:
                                latest = bucket.start_date
            self.latest_energy_bucket = latest
        self._notify()


def device_info(system_id: str, device_uuid: str) -> DeviceInfo:
    """Use the existing ecoTEC device registry identifier; do not make a new hub."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{system_id}_device_{device_uuid}")}
    )


class CloudConnectionEntity(BinarySensorEntity):
    """Binary connection state: on=API accessible, off=failed request."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, system_id: str, device_uuid: str, health: CloudHealth) -> None:
        self._system_id = system_id
        self._device_uuid = device_uuid
        self.health = health
        self._attr_name = "Vaillant Cloud Connection State"
        self._attr_unique_id = (
            f"{DOMAIN}_{system_id}_{device_uuid}_cloud_connection_state"
        )
        self._attr_device_info = device_info(system_id, device_uuid)

    @property
    def available(self) -> bool:
        # A failed coordinator request is the very state being reported.
        return True

    @property
    def is_on(self) -> bool:
        return self.health.cloud_connected

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"failure_reason": self.health.failure_reason}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.health.async_add_listener(self.async_write_ha_state)
        )


class CloudDiagnosticSensor(SensorEntity):
    """Read-only diagnostic readings from the existing polling coordinator."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    TYPES: dict[str, tuple[str, bool]] = {
        "last_api_contact": ("Last Successful API Contact", True),
        "last_energy_contact": ("Last Successful Energy Fetch", True),
        "latest_energy_bucket": ("Latest Energy Hour", True),
        "consecutive_failures": ("Cloud Consecutive Failures", False),
    }

    def __init__(
        self,
        system_id: str,
        device_uuid: str,
        health: CloudHealth,
        kind: str,
    ) -> None:
        self.health = health
        self.kind = kind
        label, timestamp = self.TYPES[kind]
        self._attr_name = f"Vaillant {label}"
        self._attr_unique_id = f"{DOMAIN}_{system_id}_{device_uuid}_{kind}"
        self._attr_device_info = device_info(system_id, device_uuid)
        if timestamp:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime | int | None:
        return getattr(self.health, self.kind)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.health.async_add_listener(self.async_write_ha_state)
        )


def create_cloud_binary_sensors(
    hass: HomeAssistant, entry_id: str
) -> list[CloudConnectionEntity]:
    entry = hass.data[DOMAIN][entry_id]
    health: CloudHealth = entry["cloud_health"]
    return [
        CloudConnectionEntity(system.id, system.devices[0].device_uuid, health)
        for system in entry["system_coordinator"].data or []
        if system.devices
    ]


def create_cloud_diagnostic_sensors(
    hass: HomeAssistant, entry_id: str
) -> list[CloudDiagnosticSensor]:
    entry = hass.data[DOMAIN][entry_id]
    health: CloudHealth = entry["cloud_health"]
    return [
        CloudDiagnosticSensor(
            system.id, system.devices[0].device_uuid, health, kind
        )
        for system in entry["system_coordinator"].data or []
        if system.devices
        for kind in CloudDiagnosticSensor.TYPES
    ]
