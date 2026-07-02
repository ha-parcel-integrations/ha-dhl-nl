"""Config flow for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DhlApiClient, DhlApiError, DhlAuthError
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    REFRESH_INTERVAL_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

_DELIVERED_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DELIVERED_FILTER_TYPE, default=DEFAULT_DELIVERED_FILTER_TYPE): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["days", "parcels"],
                translation_key=CONF_DELIVERED_FILTER_TYPE,
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Required(CONF_DELIVERED_FILTER_AMOUNT, default=DEFAULT_DELIVERED_FILTER_AMOUNT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=365,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }
)


class DhlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the DHL integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._password: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DhlOptionsFlowHandler:
        """Return the options flow handler."""
        return DhlOptionsFlowHandler()

    async def _validate_credentials(self, email: str, password: str) -> None:
        """Validate credentials against the live DHL API using the HA-managed session."""
        session = async_get_clientsession(self.hass)
        client = DhlApiClient(email, password, session)
        await client.async_login()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the credential form and validate on submit."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            try:
                await self._validate_credentials(email, password)
            except DhlAuthError:
                errors["base"] = "invalid_auth"
            except (DhlApiError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(email)
                self._abort_if_unique_id_configured()
                self._email = email
                self._password = password
                return await self.async_step_delivered()

        return self.async_show_form(
            step_id="user",
            data_schema=_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_delivered(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the delivered parcels filter form."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._email,
                data={CONF_EMAIL: self._email, CONF_PASSWORD: self._password},
                options={
                    CONF_DELIVERED_FILTER_TYPE: user_input[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(user_input[CONF_DELIVERED_FILTER_AMOUNT]),
                },
            )

        return self.async_show_form(
            step_id="delivered",
            data_schema=_DELIVERED_SCHEMA,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Initiate re-authentication for an existing config entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the re-auth credential form and update the existing entry on success."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            try:
                await self._validate_credentials(email, password)
            except DhlAuthError:
                errors["base"] = "invalid_auth"
            except (DhlApiError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            else:
                # Guard against re-authenticating with a *different* DHL
                # account — the entry (and all its entities) belong to the
                # original account's unique_id.
                await self.async_set_unique_id(email)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data={CONF_EMAIL: email, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_USER_SCHEMA,
            errors=errors,
        )


class DhlOptionsFlowHandler(OptionsFlow):
    """Handle DHL options — delivered-parcels filter plus polling cadence.

    The form is rendered with two collapsible sections (``delivered`` and
    ``polling``) so the unrelated knobs don't compete for attention. HA
    returns the user input nested by section name; we flatten it before
    storing on the config entry so the coordinator can keep reading the
    flat keys directly.

    Modern HA exposes ``self.config_entry`` on ``OptionsFlow`` automatically,
    so no constructor is needed to store it.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            delivered = user_input.get("delivered", {})
            history = user_input.get("history", {})
            polling = user_input.get("polling", {})
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return self.async_create_entry(
                title="",
                data={
                    CONF_DELIVERED_FILTER_TYPE: delivered[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(delivered[CONF_DELIVERED_FILTER_AMOUNT]),
                    CONF_INCLUDE_HISTORY: bool(history[CONF_INCLUDE_HISTORY]),
                    CONF_REFRESH_INTERVAL: int(polling[CONF_REFRESH_INTERVAL]),
                },
            )

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("delivered"): section(
                        vol.Schema(
                            {
                                vol.Required(
                                    CONF_DELIVERED_FILTER_TYPE,
                                    default=current.get(
                                        CONF_DELIVERED_FILTER_TYPE,
                                        DEFAULT_DELIVERED_FILTER_TYPE,
                                    ),
                                ): selector.SelectSelector(
                                    selector.SelectSelectorConfig(
                                        options=[
                                            selector.SelectOptionDict(value="days", label="Days"),
                                            selector.SelectOptionDict(value="parcels", label="Number of parcels"),
                                        ],
                                        mode=selector.SelectSelectorMode.LIST,
                                    )
                                ),
                                vol.Required(
                                    CONF_DELIVERED_FILTER_AMOUNT,
                                    default=current.get(
                                        CONF_DELIVERED_FILTER_AMOUNT,
                                        DEFAULT_DELIVERED_FILTER_AMOUNT,
                                    ),
                                ): selector.NumberSelector(
                                    selector.NumberSelectorConfig(
                                        min=1,
                                        max=365,
                                        step=1,
                                        mode=selector.NumberSelectorMode.BOX,
                                    )
                                ),
                            }
                        ),
                        {"collapsed": False},
                    ),
                    vol.Required("history"): section(
                        vol.Schema(
                            {
                                vol.Required(
                                    CONF_INCLUDE_HISTORY,
                                    default=current.get(
                                        CONF_INCLUDE_HISTORY,
                                        DEFAULT_INCLUDE_HISTORY,
                                    ),
                                ): selector.BooleanSelector(),
                            }
                        ),
                        {"collapsed": True},
                    ),
                    vol.Required("polling"): section(
                        vol.Schema(
                            {
                                vol.Required(
                                    CONF_REFRESH_INTERVAL,
                                    # str(): the selector's option values are
                                    # strings, so the default must be a string
                                    # too — a stored int won't match and trips
                                    # "expected str" validation on submit.
                                    default=str(current.get(
                                        CONF_REFRESH_INTERVAL,
                                        DEFAULT_REFRESH_INTERVAL,
                                    )),
                                ): selector.SelectSelector(
                                    selector.SelectSelectorConfig(
                                        options=[str(m) for m in REFRESH_INTERVAL_OPTIONS],
                                        translation_key=CONF_REFRESH_INTERVAL,
                                        mode=selector.SelectSelectorMode.DROPDOWN,
                                    )
                                ),
                            }
                        ),
                        {"collapsed": True},
                    ),
                }
            ),
        )
