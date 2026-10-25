"""User Activity Log: see who used Home Assistant, when, and what they did."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from pathlib import Path
import time
from typing import Any

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from . import websocket
from .const import (
    CONF_RETENTION_DAYS,
    CONF_TRACK_INDIRECT,
    CONF_TRACK_SESSIONS,
    DB_FILE,
    DEFAULT_OPTIONS,
    DOMAIN,
    FLUSH_INTERVAL_SECONDS,
    PANEL_COMPONENT,
    PANEL_JS,
    PANEL_URL_PATH,
    TRACKER_JS,
    URL_BASE,
)
from .store import ActivityStore
from .tracker import EventTracker, FailedLoginHandler, SessionTracker

_LOGGER = logging.getLogger(__name__)

_REGISTERED_KEY = f"{DOMAIN}_registered"


@dataclass
class Runtime:
    """Objects shared by the integration while it is loaded."""

    store: ActivityStore
    options: dict[str, Any]


async def _async_register_once(hass: HomeAssistant) -> None:
    """Static files and WebSocket commands cannot be unregistered, so add them once."""
    if hass.data.get(_REGISTERED_KEY):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(Path(__file__).parent / "frontend"), False)]
    )
    websocket.async_register(hass)
    hass.data[_REGISTERED_KEY] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up User Activity Log from a config entry."""
    options = {**DEFAULT_OPTIONS, **entry.options}

    store = ActivityStore(hass, hass.config.path(DB_FILE))
    await store.async_open()
    hass.data[DOMAIN] = Runtime(store, options)

    await _async_register_once(hass)

    entry.async_on_unload(
        EventTracker(hass, store, options[CONF_TRACK_INDIRECT]).async_start()
    )
    sessions = SessionTracker(hass, store, options[CONF_TRACK_SESSIONS])
    entry.async_on_unload(await sessions.async_start())
    entry.async_on_unload(FailedLoginHandler(hass, store).async_attach())

    entry.async_on_unload(
        async_track_time_interval(
            hass, store.async_flush, timedelta(seconds=FLUSH_INTERVAL_SECONDS)
        )
    )

    async def _async_stop(_event: Event) -> None:
        await store.async_flush()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))

    retention = timedelta(days=int(options[CONF_RETENTION_DAYS]))

    async def _async_purge(_now: Any = None) -> None:
        removed = await store.async_purge(time.time() - retention.total_seconds())
        if removed:
            _LOGGER.debug("Purged %s old activity rows", removed)

    await _async_purge()
    entry.async_on_unload(
        async_track_time_interval(hass, _async_purge, timedelta(hours=6))
    )

    frontend.add_extra_js_url(hass, TRACKER_JS)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="Activity Log",
        sidebar_icon="mdi:account-clock",
        module_url=PANEL_JS,
        require_admin=True,
    )

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    frontend.remove_extra_js_url(hass, TRACKER_JS)
    runtime: Runtime | None = hass.data.pop(DOMAIN, None)
    if runtime is not None:
        await runtime.store.async_close()
    return True
