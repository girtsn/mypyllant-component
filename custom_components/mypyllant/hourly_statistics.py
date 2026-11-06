"""Recorder-only energy publishing for controllerless Vaillant boilers.

Keep exactly the same external statistic IDs as the previous release.
No duplicate daily Home Assistant energy counters are registered.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .coordinator import DailyDataCoordinator
from .sensor import DataSensor
from .const import CONTROLLERLESS_ENERGY_NAMES, energy_profile_type

_LOGGER = logging.getLogger(__name__)

SUPPORTED_PROFILES = CONTROLLERLESS_ENERGY_NAMES


class _StatisticsOnlyPublisher(DataSensor):
    """Reuse the tested hourly recorder writer without adding a HA sensor."""

    @property
    def name(self) -> str:
        if (data := self.device_data) is None:
            return "Vaillant Energy"
        energy_type = energy_profile_type(data)
        if energy_type is None:
            return "Vaillant Energy"
        return SUPPORTED_PROFILES.get(
            (energy_type, data.operation_mode), "Vaillant Energy"
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
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._tasks: set[asyncio.Task] = set()
        self._publishers: list[_StatisticsOnlyPublisher] = []
        self._profile_ids: set[str] = set()
        self._refresh_metadata_pending = True

    def _build_publishers(self) -> None:
        self._publishers = []
        self._profile_ids = set()
        for system_id, system_data in (self.coordinator.data or {}).items():
            for device_index, data_set in enumerate(system_data["devices_data"]):
                for data_index, data in enumerate(data_set):
                    if (
                        energy_profile_type(data),
                        data.operation_mode,
                    ) not in SUPPORTED_PROFILES:
                        continue
                    publisher = _StatisticsOnlyPublisher(
                        system_id, device_index, data_index, self.coordinator
                    )
                    publisher.hass = self.hass
                    if publisher.unique_id is None:
                        continue
                    self._publishers.append(publisher)
                    self._profile_ids.add(publisher.unique_id)

    def profile_statistics(self) -> dict[tuple[str, str], str]:
        """Map stable energy profile types to their EXISTING Recorder IDs.

        This deliberately does not depend on old or new display names.
        """
        if not self._publishers:
            self._build_publishers()
        ids: dict[tuple[str, str], str] = {}
        for publisher in self._publishers:
            data = publisher.device_data
            if data is None:
                continue
            key = (energy_profile_type(data), data.operation_mode)
            metadata = publisher.hourly_statistic_metadata()
            if key not in SUPPORTED_PROFILES or metadata is None:
                continue
            if key in ids and ids[key] != metadata["statistic_id"]:
                raise ValueError("duplicate_energy_profile")
            ids[key] = metadata["statistic_id"]
        return ids

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
            # Home Assistant's external-statistics import updates metadata
            # even when no statistical rows accompany it. Refresh the name
            # explicitly instead of rewriting an existing cumulative sum just
            # to rename its series. The original IDs and values are preserved.
            if self._refresh_metadata_pending and self._publishers:
                refreshed = 0
                for publisher in self._publishers:
                    if publisher.refresh_hourly_statistic_metadata():
                        refreshed += 1
                if refreshed:
                    self._refresh_metadata_pending = False
                    _LOGGER.info(
                        "Queued metadata-only display-name updates for %d Vaillant "
                        "energy statistics (no energy samples rewritten)",
                        refreshed,
                    )
            for publisher in self._publishers:
                await publisher._safe_write_hourly_statistics()

    async def async_stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def remove_obsolete_entities(self, active_sensor_unique_ids: set[str]) -> int:
        """Clean obsolete control entities and positively identified old counters.

        Never treat a missing initial hourly API response as evidence that an
        existing sensor is obsolete. User-disabled and intermittently
        unavailable boiler diagnostics must not be removed.
        """
        if not active_sensor_unique_ids:
            _LOGGER.warning("Skipping controllerless cleanup: no active sensor IDs")
            return 0
        registry = er.async_get(self.hass)
        removed = 0
        for entry in er.async_entries_for_config_entry(registry, self.entry_id):
            # Controllerless mode also registers a real cloud-connectivity
            # binary sensor. Never remove any currently provided entity.
            if entry.unique_id in active_sensor_unique_ids:
                continue
            if entry.domain == "sensor":
                # Legacy DataSensor IDs ended in hourly profile/device indexes;
                # efficiency entities used this explicit suffix. No other
                # unknown sensors are deleted by the migration.
                parts = entry.unique_id.rsplit("_", 2)
                old_daily_counter = (
                    entry.unique_id.startswith("mypyllant_")
                    and len(parts) == 3
                    and parts[-1].isdecimal()
                    and parts[-2].isdecimal()
                )
                old_efficiency = entry.unique_id.endswith("_heating_energy_efficiency")
                if not (old_daily_counter or old_efficiency):
                    continue
            # Controllerless installations expose only the sensor platform.
            registry.async_remove(entry.entity_id)
            removed += 1
        if removed:
            _LOGGER.info(
                "Removed %d confirmed legacy controllerless entities; "
                "Recorder statistics unchanged",
                removed,
            )
        return removed
