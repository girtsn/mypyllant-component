from __future__ import annotations

import logging
from datetime import datetime as dt, timedelta
import voluptuous as vol
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    SupportsResponse,
    ServiceCall,
    ServiceResponse,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import selector

from myPyllant import export, report

from .controllerless_api import ControllerlessMyPyllantAPI
from myPyllant.const import DEFAULT_BRAND
from myPyllant.enums import DeviceDataBucketResolution
from myPyllant.http_client import (
    AuthenticationFailed,
    RealmInvalid,
    LoginEndpointInvalid,
)
from myPyllant.tests import generate_test_data
from .const import (
    DEFAULT_COUNTRY,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    OPTION_BRAND,
    OPTION_COUNTRY,
    OPTION_UPDATE_INTERVAL,
    SERVICE_GENERATE_TEST_DATA,
    SERVICE_EXPORT,
    SERVICE_REPORT,
    OPTION_UPDATE_INTERVAL_DAILY,
    DEFAULT_UPDATE_INTERVAL_DAILY,
)
from .coordinator import SystemCoordinator, DailyDataCoordinator
from .hourly_statistics import HourlyStatisticsManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.CLIMATE,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

_DEVICE_DATA_BUCKET_RESOLUTION_OPTIONS = [
    selector.SelectOptionDict(value=v.value, label=v.value.title())
    for v in DeviceDataBucketResolution
]


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        """
        from homeassistant.helpers.entity_registry import async_migrate_entries, RegistryEntry
        from homeassistant.helpers.device_registry import async_entries_for_config_entry
        from homeassistant.core import callback

        devices = async_entries_for_config_entry(
            hass.data["device_registry"], config_entry.entry_id
        )

        @callback
        def update_unique_id(entity_entry: RegistryEntry):
            return {"new_unique_id": entity_entry.unique_id} # change entity_entry.unique_id

        await async_migrate_entries(hass, config_entry.entry_id, update_unique_id)
        config_entry.version = 2 # set to new version
        """

    _LOGGER.debug("Migration to version %s successful", config_entry.version)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    username: str = entry.data.get("username")  # type: ignore
    password: str = entry.data.get("password")  # type: ignore
    update_interval = entry.options.get(OPTION_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    update_interval_daily = entry.options.get(
        OPTION_UPDATE_INTERVAL_DAILY, DEFAULT_UPDATE_INTERVAL_DAILY
    )
    country = entry.options.get(
        OPTION_COUNTRY, entry.data.get(OPTION_COUNTRY, DEFAULT_COUNTRY)
    )
    brand = entry.options.get(OPTION_BRAND, entry.data.get(OPTION_BRAND, DEFAULT_BRAND))

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    _LOGGER.debug("Creating API and logging in with %s in realm %s", username, country)
    api = ControllerlessMyPyllantAPI(
        username=username, password=password, brand=brand, country=country
    )
    try:
        await api.login()
    except (AuthenticationFailed, LoginEndpointInvalid, RealmInvalid) as e:
        raise ConfigEntryAuthFailed from e

    system_coordinator = SystemCoordinator(
        hass, api, entry, timedelta(seconds=update_interval)
    )
    _LOGGER.debug("Refreshing SystemCoordinator")
    await system_coordinator.async_refresh()
    hass.data[DOMAIN][entry.entry_id]["system_coordinator"] = system_coordinator

    controllerless = bool(
        system_coordinator.data
        and all(
            system.control_identifier.is_unsupported
            for system in system_coordinator.data
        )
    )
    hass.data[DOMAIN][entry.entry_id]["controllerless"] = controllerless
    # Earlier releases could leave this unset, which fetches energy once only.
    # Hourly collection must remain active even if obsolete daily sensors vanish.
    if controllerless and not update_interval_daily:
        update_interval_daily = 60 * 60

    # Daily data coordinator is fetched once by default (to get all entities), but not updated on a regular basis
    # to prevent quota errors
    daily_data_coordinator = DailyDataCoordinator(
        hass,
        api,
        entry,
        timedelta(seconds=update_interval_daily) if update_interval_daily else None,
    )
    _LOGGER.debug("Refreshing DailyDataCoordinator")
    await daily_data_coordinator.async_refresh()
    hass.data[DOMAIN][entry.entry_id]["daily_data_coordinator"] = daily_data_coordinator

    # No Tado/OpenTherm control operations are offered as Vaillant controls:
    # the VR 921 EMF endpoint exposes energy data but not controller controls.
    active_platforms = [Platform.SENSOR] if controllerless else PLATFORMS
    hass.data[DOMAIN][entry.entry_id]["platforms"] = active_platforms
    await hass.config_entries.async_forward_entry_setups(entry, active_platforms)

    if controllerless:
        manager = HourlyStatisticsManager(hass, daily_data_coordinator, entry.entry_id)
        hass.data[DOMAIN][entry.entry_id]["hourly_statistics_manager"] = manager
        await manager.async_start()
        # Compare the existing registry against the exact entities actually
        # offered by the controllerless setup, including the four new today
        # readings. Do not remove supported (even user-disabled) entities.
        from .sensor import (
            create_system_sensors,
            create_controllerless_energy_sensors,
        )

        expected = {
            sensor.unique_id
            for sensor in [
                *(await create_system_sensors(hass, entry)),
                *create_controllerless_energy_sensors(hass, entry),
            ]
            if isinstance(sensor, SensorEntity) and sensor.unique_id is not None
        }
        manager.remove_obsolete_entities(expected)

    async def handle_export(call: ServiceCall) -> ServiceResponse:
        _LOGGER.debug("Exporting data with params %s", call.data)
        return {
            "export": await export.main(
                user=username,
                password=password,
                brand=brand,
                country=country,
                data=call.data.get("data", False),
                resolution=call.data.get("resolution", DeviceDataBucketResolution.DAY),
                start=call.data.get("start"),
                end=call.data.get("end"),
            )
        }

    async def handle_generate_test_data(call: ServiceCall) -> ServiceResponse:
        return await generate_test_data.main(
            user=username,
            password=password,
            brand=brand,
            country=country,
            write_results=False,
        )

    async def handle_report(call: ServiceCall) -> ServiceResponse:
        return {
            f.file_name: f.file_content
            for f in await report.main(
                user=username,
                password=password,
                brand=brand,
                country=country,
                year=int(call.data.get("year", dt.now().year)),
                write_results=False,
            )
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT,
        handle_export,
        schema=vol.Schema(
            {
                vol.Optional("data"): bool,
                vol.Optional("resolution"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_DEVICE_DATA_BUCKET_RESOLUTION_OPTIONS,
                        mode=selector.SelectSelectorMode.LIST,
                    ),
                ),
                vol.Optional("start"): vol.Coerce(dt.fromisoformat),
                vol.Optional("end"): vol.Coerce(dt.fromisoformat),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_TEST_DATA,
        handle_generate_test_data,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REPORT,
        handle_report,
        schema=vol.Schema(
            {
                vol.Required("year", default=dt.now().year): vol.Coerce(int),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    active_platforms = data.get("platforms", PLATFORMS)
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, active_platforms
    )
    if unload_ok:
        if manager := data.get("hourly_statistics_manager"):
            await manager.async_stop()
        await hass.data[DOMAIN][entry.entry_id][
            "system_coordinator"
        ].api.aiohttp_session.close()
        await hass.data[DOMAIN][entry.entry_id][
            "daily_data_coordinator"
        ].api.aiohttp_session.close()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
