"""Conservative read-only VR 921 gateway diagnostics for controllerless boilers.

Uses known cloud GET endpoints, never controller actions. Diagnostic failures
must never indicate a healthy boiler and must not affect hourly energy Recorder.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re

from aiohttp import ClientResponseError
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .connectivity_diagnostics import device_info
from .coordinator import SystemCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_GATEWAY_REFRESH = timedelta(hours=4)
_QUOTA_PAUSE = timedelta(hours=4)
_SAFE_STATUS = re.compile(r"[A-Z][A-Z_0-9]{0,39}\Z")


# The upstream myPyllant fixtures show device buckets containing:
# {"codes": [{"code": "3", "type": "MAINTENANCE", "title": "...",
#             "description": "...", "occurrenceTimestamp": "..."}]}
# Never copy serialNumber, articleNumber, raw device objects or arbitrary fields.
_FAULT_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}\Z")
_MAX_FAULT_DETAILS = 20


def _safe_fault_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if not value.isprintable() or "@" in value or "://" in value:
        return None
    return value


def _fault_details(payload: list) -> tuple[int | None, list[dict], bool]:
    """Keep a bounded allowlist of fault details while counting all valid codes."""
    if not all(
        isinstance(device, dict) and isinstance(device.get("codes"), list)
        for device in payload
    ):
        return None, [], False
    count = 0
    details: list[dict] = []
    for device in payload:
        for fault in device["codes"]:
            count += 1
            if not isinstance(fault, dict) or len(details) >= _MAX_FAULT_DETAILS:
                continue
            code = fault.get("code")
            if not isinstance(code, str) or not _FAULT_CODE.fullmatch(code):
                continue
            item = {"code": code}
            typ = fault.get("type")
            if isinstance(typ, str) and _SAFE_STATUS.fullmatch(typ):
                item["type"] = typ
            title = _safe_fault_text(fault.get("title"), limit=160)
            description = _safe_fault_text(fault.get("description"), limit=320)
            if title is not None:
                item["title"] = title
            if description is not None:
                item["description"] = description
            stamp = fault.get("occurrenceTimestamp")
            if isinstance(stamp, str) and len(stamp) <= 40:
                try:
                    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        item["occurred_at"] = parsed.isoformat()
                except ValueError:
                    pass
            details.append(item)
    return count, details, count > len(details)


class GatewayDiagnosticsCoordinator(DataUpdateCoordinator):
    """Poll three verified controller-independent GET routes every four hours."""

    def __init__(
        self, hass: HomeAssistant, system_coordinator: SystemCoordinator
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Vaillant VR 921 gateway diagnostics",
            update_interval=_GATEWAY_REFRESH,
        )
        self.system_coordinator = system_coordinator
        self._pause_until: datetime | None = None

    async def _request(self, url: str):
        api = self.system_coordinator.api
        async with api.aiohttp_session.get(
            url, headers=api.get_authorized_headers()
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _async_update_data(self):
        if self._pause_until and datetime.now(timezone.utc) < self._pause_until:
            raise UpdateFailed(
                "Vaillant diagnostic requests paused after authentication/quota error"
            )

        systems = list(self.system_coordinator.data or [])
        if not systems:
            return {}

        try:
            # Reuse the existing Vaillant OAuth session and refresh policy.
            await self.system_coordinator._refresh_session()
            base = await self.system_coordinator.api.get_api_base()
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed("Unable to prepare Vaillant diagnostic request") from err

        result = {}
        for system in systems[:5]:
            sid = system.id
            readings = {
                "connected": None,
                "fault_count": None,
                "fault_details": [],
                "fault_details_truncated": False,
                "eebus_enabled": None,
                "eebus_capable": None,
                "eebus_status": None,
            }
            endpoints = (
                ("connection", f"{base}/systems/{sid}/meta-info/connection-status"),
                ("faults", f"{base}/systems/{sid}/diagnostic-trouble-codes"),
                ("eebus", f"{base}/ship/{sid}/self"),
            )
            for kind, url in endpoints:
                try:
                    payload = await self._request(url)
                except ClientResponseError as err:
                    if err.status in (401, 403, 429):
                        # Avoid hammering the service; do not expose error URL/body.
                        self._pause_until = datetime.now(timezone.utc) + _QUOTA_PAUSE
                        raise UpdateFailed(
                            f"Vaillant gateway diagnostics paused (HTTP {err.status})"
                        ) from err
                    _LOGGER.debug(
                        "Vaillant %s diagnostics unavailable (HTTP %s)",
                        kind,
                        err.status,
                    )
                    continue
                except Exception as err:
                    _LOGGER.debug(
                        "Vaillant %s diagnostics failed (%s)", kind, type(err).__name__
                    )
                    continue

                if kind == "connection" and isinstance(payload, dict):
                    connected = payload.get("connected")
                    if type(connected) is bool:
                        readings["connected"] = connected
                elif kind == "faults" and isinstance(payload, list):
                    count, details, truncated = _fault_details(payload)
                    readings["fault_count"] = count
                    readings["fault_details"] = details
                    readings["fault_details_truncated"] = truncated
                elif kind == "eebus" and isinstance(payload, dict):
                    enabled = payload.get("spineEnabled")
                    capable = payload.get("spineCapable")
                    status = payload.get("spineEnabledStatus")
                    if type(enabled) is bool:
                        readings["eebus_enabled"] = enabled
                    if type(capable) is bool:
                        readings["eebus_capable"] = capable
                    if isinstance(status, str) and _SAFE_STATUS.fullmatch(status):
                        readings["eebus_status"] = status
            result[sid] = readings
        self._pause_until = None
        return result


class GatewayDiagnosticBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """One verified cloud diagnostic, attached to the existing ecoTEC device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    KINDS = {
        "connected": ("Gateway Online", BinarySensorDeviceClass.CONNECTIVITY),
        "fault_count": ("Reported Faults", BinarySensorDeviceClass.PROBLEM),
        "eebus_enabled": ("EEBUS Enabled", None),
    }

    def __init__(
        self,
        coordinator: GatewayDiagnosticsCoordinator,
        system_id: str,
        device_uuid: str,
        kind: str,
    ) -> None:
        super().__init__(coordinator)
        self._system_id = system_id
        self._kind = kind
        title, device_class = self.KINDS[kind]
        self._attr_name = f"Vaillant {title}"
        self._attr_unique_id = f"{DOMAIN}_{system_id}_{device_uuid}_gateway_{kind}"
        self._attr_device_info = device_info(system_id, device_uuid)
        if device_class is not None:
            self._attr_device_class = device_class

    @property
    def _reading(self):
        data = self.coordinator.data
        if not isinstance(data, dict):
            return {}
        return data.get(self._system_id) or {}

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        value = self._reading.get(self._kind)
        if self._kind == "fault_count":
            return type(value) is int and value >= 0
        return type(value) is bool

    @property
    def is_on(self) -> bool | None:
        if not self.available:
            return None
        value = self._reading[self._kind]
        return value > 0 if self._kind == "fault_count" else value

    @property
    def extra_state_attributes(self) -> dict:
        if not self.available:
            return {}
        if self._kind == "fault_count":
            return {
                "reported_fault_count": self._reading["fault_count"],
                "reported_fault_codes": self._reading.get("fault_details", []),
                "fault_codes_truncated": self._reading.get(
                    "fault_details_truncated", False
                ),
            }
        if self._kind == "eebus_enabled":
            return {
                "spine_capable": self._reading.get("eebus_capable"),
                "spine_enabled_status": self._reading.get("eebus_status"),
                "note": "Gateway interface status, not proof of paired EEBUS devices",
            }
        return {}


def create_gateway_binary_sensors(
    hass: HomeAssistant, entry_id: str
) -> list[GatewayDiagnosticBinarySensor]:
    """Create three diagnostics per detected ecoTEC, without new devices."""
    record = hass.data[DOMAIN][entry_id]
    coordinator = record.get("gateway_diagnostics")
    if coordinator is None:
        return []
    return [
        GatewayDiagnosticBinarySensor(
            coordinator, system.id, system.devices[0].device_uuid, kind
        )
        for system in record["system_coordinator"].data or []
        if system.devices
        for kind in GatewayDiagnosticBinarySensor.KINDS
    ]
