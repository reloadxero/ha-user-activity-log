"""SQLite-backed storage for activity rows."""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import sqlite3
import threading
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id TEXT,
    user_name TEXT,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_id TEXT,
    details TEXT,
    context_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id, id);
CREATE INDEX IF NOT EXISTS idx_events_category ON events (category, id);
"""

REDACT_KEYS = {
    "code",
    "password",
    "pin",
    "token",
    "access_token",
    "api_key",
    "secret",
}
MAX_DETAILS_CHARS = 8000


def sanitize(value: Any, depth: int = 0) -> Any:
    """Make a value JSON friendly, small and free of obvious secrets."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {
            str(k): "**redacted**"
            if str(k).lower() in REDACT_KEYS
            else sanitize(v, depth + 1)
            for k, v in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize(v, depth + 1) for v in list(value)[:50]]
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:500]


def _dump_details(details: dict[str, Any] | None) -> str | None:
    if not details:
        return None
    text = json.dumps(details, default=str, ensure_ascii=False)
    if len(text) > MAX_DETAILS_CHARS:
        text = json.dumps({"truncated": text[:MAX_DETAILS_CHARS]})
    return text


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    if data.get("details"):
        try:
            data["details"] = json.loads(data["details"])
        except ValueError:
            pass
    return data


class ActivityStore:
    """Buffers activity rows in memory and writes them to SQLite in batches."""

    def __init__(self, hass: HomeAssistant, path: str) -> None:
        self.hass = hass
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._queue: list[dict[str, Any]] = []
        self._subscribers: set[Callable[[list[dict[str, Any]]], None]] = set()
        self._user_names: dict[str, str | None] = {}

    # --- lifecycle -------------------------------------------------------

    def _open(self) -> None:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn

    async def async_open(self) -> None:
        """Open the database."""
        await self.hass.async_add_executor_job(self._open)

    def _close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    async def async_close(self) -> None:
        """Flush pending rows and close the database."""
        await self.async_flush()
        await self.hass.async_add_executor_job(self._close)

    # --- writing ---------------------------------------------------------

    @callback
    def add(
        self,
        category: str,
        action: str,
        *,
        user_id: str | None = None,
        user_name: str | None = None,
        entity_id: str | None = None,
        details: dict[str, Any] | None = None,
        context_id: str | None = None,
        ts: float | None = None,
    ) -> None:
        """Queue a row; it is written on the next flush."""
        self._queue.append(
            {
                "ts": ts if ts is not None else time.time(),
                "user_id": user_id,
                "user_name": user_name,
                "category": category,
                "action": action[:300],
                "entity_id": entity_id,
                "details": sanitize(details) if details else None,
                "context_id": context_id,
            }
        )

    @callback
    def clear_user_cache(self) -> None:
        """Forget cached user names (called when users change)."""
        self._user_names.clear()

    async def _async_user_name(self, user_id: str) -> str | None:
        if user_id not in self._user_names:
            user = await self.hass.auth.async_get_user(user_id)
            self._user_names[user_id] = user.name if user else None
        return self._user_names[user_id]

    def _insert(self, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            if self._conn is None:
                return
            for row in rows:
                cur = self._conn.execute(
                    "INSERT INTO events (ts, user_id, user_name, category, action,"
                    " entity_id, details, context_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["ts"],
                        row["user_id"],
                        row["user_name"],
                        row["category"],
                        row["action"],
                        row["entity_id"],
                        _dump_details(row["details"]),
                        row["context_id"],
                    ),
                )
                row["id"] = cur.lastrowid
            self._conn.commit()

    async def async_flush(self, *_: Any) -> None:
        """Write queued rows and notify live subscribers."""
        if not self._queue or self._conn is None:
            return
        rows, self._queue = self._queue, []
        for row in rows:
            if row["user_id"] and not row["user_name"]:
                row["user_name"] = await self._async_user_name(row["user_id"])
        try:
            await self.hass.async_add_executor_job(self._insert, rows)
        except sqlite3.Error:
            _LOGGER.exception("Failed to write %s activity rows", len(rows))
            return
        for subscriber in list(self._subscribers):
            try:
                subscriber(rows)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Activity subscriber failed")

    @callback
    def async_subscribe(
        self, subscriber: Callable[[list[dict[str, Any]]], None]
    ) -> Callable[[], None]:
        """Receive every batch of newly written rows."""
        self._subscribers.add(subscriber)

        @callback
        def unsubscribe() -> None:
            self._subscribers.discard(subscriber)

        return unsubscribe

    # --- reading ---------------------------------------------------------

    def _query(
        self,
        limit: int,
        before_id: int | None,
        user_id: str | None,
        category: str | None,
        search: str | None,
        start_ts: float | None,
        end_ts: float | None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if before_id is not None:
            sql += " AND id < ?"
            params.append(before_id)
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        if category:
            sql += " AND category = ?"
            params.append(category)
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        if search:
            like = f"%{search}%"
            sql += (
                " AND (action LIKE ? OR entity_id LIKE ? OR details LIKE ?"
                " OR user_name LIKE ?)"
            )
            params.extend([like, like, like, like])
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            if self._conn is None:
                return []
            return [_row_to_dict(r) for r in self._conn.execute(sql, params)]

    async def async_query(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Return rows newest first."""
        return await self.hass.async_add_executor_job(
            lambda: self._query(
                kwargs.get("limit", 200),
                kwargs.get("before_id"),
                kwargs.get("user_id"),
                kwargs.get("category"),
                kwargs.get("search"),
                kwargs.get("start_ts"),
                kwargs.get("end_ts"),
            )
        )

    def _users(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._conn is None:
                return []
            rows = self._conn.execute(
                "SELECT user_id, user_name, COUNT(*) AS count, MAX(ts) AS last_ts"
                " FROM events WHERE user_id IS NOT NULL GROUP BY user_id"
                " ORDER BY last_ts DESC"
            )
            return [dict(r) for r in rows]

    async def async_users(self) -> list[dict[str, Any]]:
        """Return the users that appear in the log."""
        return await self.hass.async_add_executor_job(self._users)

    def _purge(self, older_than: float) -> int:
        with self._lock:
            if self._conn is None:
                return 0
            cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (older_than,))
            self._conn.commit()
            return cur.rowcount

    async def async_purge(self, older_than: float) -> int:
        """Delete rows older than the given timestamp."""
        return await self.hass.async_add_executor_job(self._purge, older_than)
