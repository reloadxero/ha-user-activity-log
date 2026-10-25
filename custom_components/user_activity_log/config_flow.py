"""Config flow for User Activity Log."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_RETENTION_DAYS,
    CONF_TRACK_INDIRECT,
    CONF_TRACK_PAGE_VIEWS,
    CONF_TRACK_SESSIONS,
    DEFAULT_OPTIONS,
    DOMAIN,
)


class UserActivityLogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="User Activity Log", data={}, options=dict(DEFAULT_OPTIONS)
            )
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return UserActivityLogOptionsFlow()


class UserActivityLogOptionsFlow(OptionsFlow):
    """Change what is tracked and how long it is kept."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RETENTION_DAYS, default=current[CONF_RETENTION_DAYS]
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=3650)),
                vol.Required(
                    CONF_TRACK_PAGE_VIEWS, default=current[CONF_TRACK_PAGE_VIEWS]
                ): bool,
                vol.Required(
                    CONF_TRACK_INDIRECT, default=current[CONF_TRACK_INDIRECT]
                ): bool,
                vol.Required(
                    CONF_TRACK_SESSIONS, default=current[CONF_TRACK_SESSIONS]
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
