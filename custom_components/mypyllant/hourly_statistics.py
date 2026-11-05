"""Recorder-only energy publishing for controllerless Vaillant boilers.

Keep exactly the same external statistic IDs as the previous release.
No duplicate daily Home Assistant energy counters are registered.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .coordinator import DailyDataCoordinator
from .sensor import DataSensor, EfficiencySensor
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SUPPORTED_PROFILES = {
    ("CONSUMED_PRIMARY_ENERGY", "HEATING"): "Vaillant Gas Heating",
    ("CONSUMED_PRIMARY_ENERGY", "DOMESTIC_HOT_WATER"): "Vaillant Gas Hot Water",
    ("CONSUMED_ELECTRICAL_ENERGY", "HEATING"): "Vaillant Electricity Heating",
    ("CONSUMED_ELECTRICAL_ENERGY", "DOMESTIC_HOT_WATER"): "Vaillant Electricity Hot Water",
}


class _StatisticsOnlyPublisher(DataSensor):
    """Reuse the tested hourly recorder writer without adding a HA sensor."""

    @property
    def name(self) -> str:
        if (data := self.device_data) is None:
            return "Vaillant Energy"
        return SUPPORTED_PROFILES.get(
            (data.energy_type, data.operation_mode), "Vaillant Energy"
        )


class HourlyStatisticsManager:
    """Publish four energy profiles to recorder, independently of sensor entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DailyDataCoordinator,
        entry_id: str,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.entry_id = entry_id
        self._lock = asyncio.Lock()
        self._unsubscribe = None
        self._tasks: set[asyncio.Task] = set()
        self._publishers: list[_StatisticsOnlyPublisher] = []
        self._profile_ids: set[str] = set()

    def _build_publishers(self) -> None:
        self._publishers = []
        self._profile_ids = set()
        for system_id, system_data in (self.coordinator.data or {}).items():
            for device_index, data_set in enumerate(system_data["devices_data"]):
                for data_index, data in enumerate(data_set):
                    if (data.energy_type, data.operation_mode) not in SUPPORTED_PROFILES:
                        continue
                    publisher = _StatisticsOnlyPublisher(
                        system_id, device_index, data_index, self.coordinator
                    )
                    publisher.hass = self.hass
                    if publisher.unique_id is None:
                        continue
                    self._publishers.append(publisher)
                    self._profile_ids.add(publisher.unique_id)

    async def async_start(self) -> None:
        self._build_publishers()
        self._unsubscribe = self.coordinator.async_add_listener(self._on_refresh)
        await self.async_publish()

    @callback
    def _on_refresh(self) -> None:
        task = self.hass.async_create_task(self.async_publish())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def async_publish(self) -> None:
        async with self._lock:
            self._build_publishers()
            for publisher in self._publishers:
                await publisher._safe_write_hourly_statistics()

    async def async_stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def remove_legacy_daily_entities(self) -> int:
        """Remove obsolete entity-registry entries, never delete statistics."""
        registry = er.async_get(self.hass)
        removed = 0
        unique_ids = set(self._profile_ids)
        for system_id, system_data in (self.coordinator.data or {}).items():
            unique_ids.add(
                EfficiencySensor(system_id, None, self.coordinator).unique_id
            )
            for device_index, data_set in enumerate(system_data["devices_data"]):
                if data_set:
                    unique_ids.add(
                        EfficiencySensor(
                            system_id, device_index, self.coordinator
                        ).unique_id
                    )
        for unique_id in unique_ids:
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            if entity_id is None:
                continue
            entry = registry.async_get(entity_id)
            if entry is None or entry.config_entry_id != self.entry_id:
                continue
            registry.async_remove(entity_id)
            removed += 1
        if removed:
            _LOGGER.info(
                "Removed %d obsolete daily energy/efficiency entities; "
                "external hourly statistics remain untouched",
                removed,
            )
        return removed
