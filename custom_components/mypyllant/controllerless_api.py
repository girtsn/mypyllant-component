"""Read-only energy discovery for systems without a Vaillant controller.

myPyllant's standard get_systems() requires a supported controller endpoint.
A VR 921 connected to an independently controlled boiler can return
controlIdentifier='none' while the controller-independent EMF currentSystem
endpoint still exposes its heat generator and energy profiles.

Construct a minimal System from that endpoint so the existing device energy
coordinator, sensors and hourly statistics machinery can be reused.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from aiohttp import ClientResponseError
from myPyllant.api import MyPyllantAPI
from myPyllant.enums import ControlIdentifier
from myPyllant.models import Home, System
from myPyllant.utils import dict_to_snake_case

_LOGGER = logging.getLogger(__name__)


class ControllerlessMyPyllantAPI(MyPyllantAPI):
    """Include EMF-capable heat generators when controller information is absent."""

    async def get_systems(
        self,
        include_connection_status: bool = False,
        include_diagnostic_trouble_codes: bool = False,
        include_rts: bool = False,
        include_mpc: bool = False,
        include_ambisense_rooms: bool = False,
        include_energy_management: bool = False,
        include_eebus: bool = False,
        include_ambisense_capability: bool = False,
        homes: list[Home] | None = None,
    ) -> AsyncIterator[System]:
        if homes is None:
            homes = [home async for home in self.get_homes()]

        normal_homes: list[Home] = []
        controllerless_homes: list[Home] = []
        for home in homes:
            try:
                control = await self.get_control_identifier(home.system_id)
            except ValueError:
                # myPyllant 0.9.18 raises for the literal API value "none".
                # Do not mask genuinely unexpected future identifiers.
                if self.control_identifiers.get(home.system_id) != "none":
                    raise
                control = ControlIdentifier.UNSUPPORTED

            if control.is_unsupported:
                controllerless_homes.append(home)
            else:
                normal_homes.append(home)

        # Preserve the upstream implementation for normal installations.
        if normal_homes:
            async for system in super().get_systems(
                include_connection_status=include_connection_status,
                include_diagnostic_trouble_codes=include_diagnostic_trouble_codes,
                include_rts=include_rts,
                include_mpc=include_mpc,
                include_ambisense_rooms=include_ambisense_rooms,
                include_energy_management=include_energy_management,
                include_eebus=include_eebus,
                include_ambisense_capability=include_ambisense_capability,
                homes=normal_homes,
            ):
                yield system

        # An unsupported or absent controller must not hide EMF energy data.
        for home in controllerless_homes:
            url = (
                f"{await self.get_api_base()}/emf/v2/"
                f"{home.system_id}/currentSystem"
            )
            try:
                async with self.aiohttp_session.get(
                    url, headers=self.get_authorized_headers()
                ) as response:
                    response.raise_for_status()
                    current_system = dict_to_snake_case(await response.json())
            except ClientResponseError as err:
                if err.status in (400, 404):
                    _LOGGER.debug(
                        "No EMF energy data for controller-free system %s: %s",
                        home.system_id,
                        err,
                    )
                    continue
                raise

            if not isinstance(current_system, dict):
                _LOGGER.debug(
                    "Unexpected EMF data for controller-free system %s",
                    home.system_id,
                )
                continue

            generators = [
                current_system.get("primary_heat_generator"),
                *(current_system.get("secondary_heat_generators") or []),
            ]
            if not any(
                isinstance(device, dict)
                and device.get("device_uuid")
                and device.get("data")
                for device in generators
            ):
                _LOGGER.debug(
                    "No heat generator energy profiles for system %s",
                    home.system_id,
                )
                continue

            _LOGGER.info(
                "Using controller-independent EMF energy data for system %s",
                home.system_id,
            )
            yield System.from_api(
                home=home,
                brand=self.brand,
                timezone=home.timezone,
                control_identifier=ControlIdentifier.UNSUPPORTED,
                state={},
                configuration={},
                properties={},
                current_system=current_system,
                ambisense_rooms=[],
                connected=(
                    await self.get_connection_status(home.system_id)
                    if include_connection_status
                    else None
                ),
            )
