from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import selector
from homeassistant.helpers.config_validation import positive_int
from myPyllant.api import (
    MyPyllantAPI,
)
from myPyllant.http_client import (
    AuthenticationFailed,
    LoginEndpointInvalid,
    RealmInvalid,
)
from myPyllant.const import COUNTRIES, DEFAULT_BRAND
from . import OPTION_UPDATE_INTERVAL_DAILY
from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    OPTION_BRAND,
    OPTION_COUNTRY,
    OPTION_UPDATE_INTERVAL,
)


_LOGGER = logging.getLogger(__name__)

# This is the schema that used to display the UI to the user. This simple
# schema has a single required host field, but it could include a number of fields
# such as username, password etc. See other components in the HA core code for
# further examples.
# Note the input displayed to the user will be translated. See the
# translations/<lang>.json file and strings.json. See here for further information:
# https://developers.home-assistant.io/docs/config_entries_config_flow_handler/#translations
# At the time of writing I found the translations created by the scaffold didn't
# quite work as documented and always gave me the "Lokalise key references" string
# (in square brackets), rather than the actual translated value. I did not attempt to
# figure this out or look further into it.

_COUNTRIES_OPTIONS = [
    selector.SelectOptionDict(value=k, label=v)
    for k, v in COUNTRIES[DEFAULT_BRAND].items()
]


DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
        vol.Optional(OPTION_COUNTRY): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_COUNTRIES_OPTIONS,
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        ),
    },
)

# No controller-only or OpenTherm control options: these are read-only
# Vaillant cloud energy and device diagnostics, not Tado thermostat controls.
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(
            OPTION_UPDATE_INTERVAL,
            default=DEFAULT_UPDATE_INTERVAL,
        ): positive_int,
        vol.Required(
            OPTION_UPDATE_INTERVAL_DAILY,
            default=60 * 60,
        ): positive_int,
    }
)


async def validate_input(hass: HomeAssistant, data: dict) -> str:
    # The product is specifically for Vaillant, not a brand picker.
    async with MyPyllantAPI(**{OPTION_BRAND: DEFAULT_BRAND, **data}) as api:
        await api.login()
        return data["username"].lower()


class OptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = OPTIONS_SCHEMA
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore
    VERSION = 1  # This needs to be changed if a migration is necessary

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                username = await validate_input(self.hass, user_input)
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=username, data=user_input)
            except AuthenticationFailed:
                errors["base"] = "authentication_failed"
            except LoginEndpointInvalid:
                errors["country"] = "login_endpoint_invalid"
            except RealmInvalid:
                errors["country"] = "realm_invalid"
            except AbortFlow:
                errors["base"] = "already_configured"
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception", exc_info=e)
                errors["base"] = "unknown"
            if "password" in user_input:
                del user_input["password"]

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(DATA_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, *args, **kwargs):
        """Perform reauthentication upon an API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        """Confirm reauthentication dialog."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                username = await validate_input(self.hass, user_input)
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
            except AuthenticationFailed:
                errors["base"] = "authentication_failed"
            except LoginEndpointInvalid:
                errors["country"] = "login_endpoint_invalid"
            except RealmInvalid:
                errors["country"] = "realm_invalid"
            except AbortFlow:
                errors["base"] = "already_configured"
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception", exc_info=e)
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates=user_input,
                )
        else:
            config_entry = self.hass.config_entries.async_get_entry(
                self.context["entry_id"]
            )
            if config_entry is None:
                return self.async_abort(reason="unknown_entry")
            user_input = dict(config_entry.data)

        if "password" in user_input:
            del user_input["password"]

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(DATA_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Handle a reconfiguration flow initialized by the user."""
        config_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if not config_entry:
            return self.async_abort(reason="entry_not_found")

        errors = {}

        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
            except AuthenticationFailed:
                errors["base"] = "authentication_failed"
            except LoginEndpointInvalid:
                errors["country"] = "login_endpoint_invalid"
            except RealmInvalid:
                errors["country"] = "realm_invalid"
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception", exc_info=e)
                errors["base"] = "unknown"
            else:
                updated_config = {**config_entry.data, **user_input}
                self.hass.config_entries.async_update_entry(
                    config_entry,
                    data=updated_config,
                )
                await self.hass.config_entries.async_reload(config_entry.entry_id)
                return self.async_abort(reason="reconfiguration_successful")
        else:
            user_input = dict(config_entry.data)

        if "password" in user_input:
            del user_input["password"]

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(DATA_SCHEMA, user_input),
            errors=errors,
        )
