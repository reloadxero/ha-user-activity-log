"""WebSocket API used by the frontend tracker and the Activity Log panel."""

from __future__ import annotations

import inspect
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import CAT_PAGE_VIEW, CONF_TRACK_PAGE_VIEWS, DOMAIN


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the WebSocket commands (once per Home Assistant run)."""
    websocket_api.async_register_command(hass, ws_page_view)
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_subscribe)


async def _refresh_token(hass: HomeAssistant, token_id: str | None) -> Any:
    if not token_id:
        return None
    result = hass.auth.async_get_refresh_token(token_id)
    if inspect.isawaitable(result):
        result = await result
    return result


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/page_view",
        vol.Required("path"): vol.All(str, vol.Length(max=500)),
        vol.Optional("title"): vol.Any(None, vol.All(str, vol.Length(max=200))),
        vol.Optional("initial"): bool,
        vol.Optional("resumed"): bool,
        vol.Optional("user_agent"): vol.All(str, vol.Length(max=500)),
    }
)
@websocket_api.async_response
async def ws_page_view(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Record that the connected user opened a frontend page."""
    runtime = hass.data.get(DOMAIN)
    if runtime is None or not runtime.options[CONF_TRACK_PAGE_VIEWS]:
        connection.send_result(msg["id"])
        return

    details: dict[str, Any] = {"title": msg.get("title")}
    if msg.get("initial"):
        details["opened"] = True
        details["user_agent"] = msg.get("user_agent")
    if msg.get("resumed"):
        details["resumed"] = True
    token = await _refresh_token(hass, connection.refresh_token_id)
    if token is not None:
        details["client"] = token.client_name or token.client_id
        if token.last_used_ip:
            details["ip"] = str(token.last_used_ip)

    user = connection.user
    runtime.store.add(
        CAT_PAGE_VIEW,
        msg["path"],
        user_id=user.id,
        user_name=user.name,
        details=details,
    )
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/list",
        vol.Optional("limit", default=200): vol.All(int, vol.Range(min=1, max=1000)),
        vol.Optional("before_id"): int,
        vol.Optional("user_id"): str,
        vol.Optional("category"): str,
        vol.Optional("search"): str,
        vol.Optional("start_ts"): vol.Coerce(float),
        vol.Optional("end_ts"): vol.Coerce(float),
    }
)
@websocket_api.async_response
async def ws_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return logged activity, newest first."""
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "User Activity Log is not loaded")
        return
    await runtime.store.async_flush()
    query = {
        k: msg[k]
        for k in ("limit", "before_id", "user_id", "category", "search", "start_ts", "end_ts")
        if k in msg
    }
    events = await runtime.store.async_query(**query)
    users = await runtime.store.async_users()
    connection.send_result(
        msg["id"],
        {"events": events, "users": users, "has_more": len(events) == msg["limit"]},
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Stream newly logged rows to the panel."""
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "User Activity Log is not loaded")
        return

    @callback
    def forward(rows: list[dict[str, Any]]) -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], {"events": rows})
        )

    connection.subscriptions[msg["id"]] = runtime.store.async_subscribe(forward)
    connection.send_result(msg["id"])
