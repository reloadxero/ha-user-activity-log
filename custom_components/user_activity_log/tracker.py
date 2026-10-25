"""Collectors that turn Home Assistant activity into log rows."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED, MATCH_ALL
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import (
    CAT_EVENT,
    CAT_INDIRECT,
    CAT_LOGIN,
    CAT_LOGIN_FAILED,
    CAT_LOGOUT,
    CAT_SERVICE,
    CAT_SESSION,
    CAT_TOKEN,
    DOMAIN,
    SESSION_POLL_SECONDS,
)
from .store import ActivityStore

_LOGGER = logging.getLogger(__name__)

# Events that carry a user context but would only duplicate a logged service call
IGNORED_EVENTS = {EVENT_STATE_CHANGED}
IGNORED_SERVICE_DOMAINS = {"system_log"}
# Events worth logging when they were caused (indirectly) by a user's action
INDIRECT_EVENTS = {EVENT_CALL_SERVICE, "automation_triggered", "script_started"}
USER_CHANGE_EVENTS = {"user_added", "user_updated", "user_removed"}

TARGET_KEYS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")
MAX_CONTEXTS = 5000

TOKEN_TYPE_SYSTEM = "system"
TOKEN_TYPE_LONG_LIVED = "long_lived_access_token"


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def _target_summary(data: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for key in TARGET_KEYS:
        parts.extend(_as_list(data.get(key)))
    return ", ".join(parts)[:500] or None


class EventTracker:
    """Logs events on the bus that originate from a user."""

    def __init__(
        self, hass: HomeAssistant, store: ActivityStore, track_indirect: bool
    ) -> None:
        self.hass = hass
        self.store = store
        self.track_indirect = track_indirect
        # context id -> user id, so automations triggered by a user can be traced
        self._contexts: OrderedDict[str, str] = OrderedDict()

    @callback
    def async_start(self) -> Callable[[], None]:
        """Start listening to the event bus."""
        return self.hass.bus.async_listen(MATCH_ALL, self._handle_event)

    @callback
    def _remember(self, context_id: str, user_id: str) -> None:
        self._contexts[context_id] = user_id
        self._contexts.move_to_end(context_id)
        if len(self._contexts) > MAX_CONTEXTS:
            self._contexts.popitem(last=False)

    def _entity_names(self, data: dict[str, Any]) -> dict[str, str]:
        names = {}
        for entity_id in _as_list(data.get("entity_id"))[:10]:
            if state := self.hass.states.get(entity_id):
                names[entity_id] = state.name
        return names

    @callback
    def _handle_event(self, event: Event) -> None:
        if event.event_type in USER_CHANGE_EVENTS:
            self.store.clear_user_cache()

        context = event.context
        user_id = context.user_id

        if user_id is None:
            if context.parent_id is None:
                return
            origin = self._contexts.get(context.parent_id) or self._contexts.get(
                context.id
            )
            if origin is None:
                return
            self._remember(context.id, origin)
            if self.track_indirect and event.event_type in INDIRECT_EVENTS:
                self._log_indirect(event, origin)
            return

        self._remember(context.id, user_id)
        if event.event_type in IGNORED_EVENTS:
            return

        if event.event_type == EVENT_CALL_SERVICE:
            domain = event.data.get("domain", "")
            if domain in IGNORED_SERVICE_DOMAINS:
                return
            service_data = event.data.get("service_data") or {}
            details: dict[str, Any] = {"data": service_data}
            if names := self._entity_names(service_data):
                details["names"] = names
            self.store.add(
                CAT_SERVICE,
                f"{domain}.{event.data.get('service', '')}",
                user_id=user_id,
                entity_id=_target_summary(service_data),
                details=details,
                context_id=context.id,
                ts=event.time_fired_timestamp,
            )
            return

        self.store.add(
            CAT_EVENT,
            event.event_type,
            user_id=user_id,
            entity_id=_target_summary(event.data),
            details={"data": dict(event.data)},
            context_id=context.id,
            ts=event.time_fired_timestamp,
        )

    @callback
    def _log_indirect(self, event: Event, user_id: str) -> None:
        data = event.data
        if event.event_type == EVENT_CALL_SERVICE:
            service_data = data.get("service_data") or {}
            action = f"{data.get('domain', '')}.{data.get('service', '')}"
            entity_id = _target_summary(service_data)
            details: dict[str, Any] = {"data": service_data}
            if names := self._entity_names(service_data):
                details["names"] = names
        else:
            kind = event.event_type.split("_")[0]
            action = f"{kind}: {data.get('name') or data.get('entity_id')}"
            entity_id = data.get("entity_id")
            details = {"data": dict(data)}
        details["parent_context"] = event.context.parent_id
        self.store.add(
            CAT_INDIRECT,
            action,
            user_id=user_id,
            entity_id=entity_id,
            details=details,
            context_id=event.context.id,
            ts=event.time_fired_timestamp,
        )


class SessionTracker:
    """Detects logins, logouts and token refreshes by polling refresh tokens."""

    def __init__(
        self, hass: HomeAssistant, store: ActivityStore, track_refresh: bool
    ) -> None:
        self.hass = hass
        self.store = store
        self.track_refresh = track_refresh
        self._persist: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.sessions")
        self._known: dict[str, dict[str, Any]] | None = None

    async def async_start(self) -> Callable[[], None]:
        """Load the last snapshot and start polling."""
        data = await self._persist.async_load()
        if data is not None:
            self._known = data.get("tokens", {})
        await self._async_poll()
        return async_track_time_interval(
            self.hass, self._async_poll, timedelta(seconds=SESSION_POLL_SECONDS)
        )

    async def _async_poll(self, _now: Any = None) -> None:
        current: dict[str, dict[str, Any]] = {}
        for user in await self.hass.auth.async_get_users():
            if user.system_generated:
                continue
            for token in list(user.refresh_tokens.values()):
                if token.token_type == TOKEN_TYPE_SYSTEM:
                    continue
                current[token.id] = {
                    "user_id": user.id,
                    "user_name": user.name,
                    "client": token.client_name or token.client_id,
                    "type": token.token_type,
                    "created": token.created_at.timestamp(),
                    "last_used": token.last_used_at.timestamp()
                    if token.last_used_at
                    else None,
                    "ip": str(token.last_used_ip) if token.last_used_ip else None,
                }

        if self._known is not None:
            self._diff(self._known, current)

        changed = self._known != current
        self._known = current
        if changed:
            self._persist.async_delay_save(lambda: {"tokens": self._known}, 10)

    @callback
    def _diff(
        self, known: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
    ) -> None:
        for token_id, info in current.items():
            details = {"client": info["client"], "ip": info["ip"]}
            prev = known.get(token_id)
            if prev is None:
                if info["type"] == TOKEN_TYPE_LONG_LIVED:
                    category, action = CAT_TOKEN, "Long-lived token created"
                else:
                    category, action = CAT_LOGIN, "Logged in"
                self.store.add(
                    category,
                    action,
                    user_id=info["user_id"],
                    user_name=info["user_name"],
                    details=details,
                    ts=info["created"],
                )
            elif (
                self.track_refresh
                and info["last_used"]
                and info["last_used"] != prev.get("last_used")
            ):
                self.store.add(
                    CAT_SESSION,
                    "Session active",
                    user_id=info["user_id"],
                    user_name=info["user_name"],
                    details=details,
                    ts=info["last_used"],
                )

        for token_id, prev in known.items():
            if token_id in current:
                continue
            self.store.add(
                CAT_LOGOUT,
                "Long-lived token removed"
                if prev.get("type") == TOKEN_TYPE_LONG_LIVED
                else "Logged out / session removed",
                user_id=prev.get("user_id"),
                user_name=prev.get("user_name"),
                details={"client": prev.get("client"), "ip": prev.get("ip")},
            )


class FailedLoginHandler(logging.Handler):
    """Captures failed-authentication warnings from the http ban module."""

    LOGGER_NAME = "homeassistant.components.http.ban"

    def __init__(self, hass: HomeAssistant, store: ActivityStore) -> None:
        super().__init__(logging.WARNING)
        self.hass = hass
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        lowered = message.lower()
        if "invalid authentication" not in lowered and "login attempt" not in lowered:
            return
        ip = None
        if isinstance(record.args, tuple) and record.args:
            ip = str(record.args[0])
        self.hass.loop.call_soon_threadsafe(self._add, message, ip)

    def _add(self, message: str, ip: str | None) -> None:
        self.store.add(
            CAT_LOGIN_FAILED,
            "Failed login / invalid authentication",
            details={"ip": ip, "message": message},
        )

    @callback
    def async_attach(self) -> Callable[[], None]:
        """Attach to the ban logger; returns a detach callback."""
        logger = logging.getLogger(self.LOGGER_NAME)
        logger.addHandler(self)

        @callback
        def detach() -> None:
            logger.removeHandler(self)

        return detach
