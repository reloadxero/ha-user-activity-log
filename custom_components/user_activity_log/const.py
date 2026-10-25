"""Constants for User Activity Log."""

from __future__ import annotations

DOMAIN = "user_activity_log"
VERSION = "0.1.0"

DB_FILE = "user_activity_log.db"

URL_BASE = "/user_activity_log_static"
TRACKER_JS = f"{URL_BASE}/tracker.js?v={VERSION}"
PANEL_JS = f"{URL_BASE}/panel.js?v={VERSION}"
PANEL_URL_PATH = "user-activity-log"
PANEL_COMPONENT = "user-activity-log-panel"

CONF_RETENTION_DAYS = "retention_days"
CONF_TRACK_PAGE_VIEWS = "track_page_views"
CONF_TRACK_INDIRECT = "track_indirect"
CONF_TRACK_SESSIONS = "track_sessions"

DEFAULT_OPTIONS = {
    CONF_RETENTION_DAYS: 30,
    CONF_TRACK_PAGE_VIEWS: True,
    CONF_TRACK_INDIRECT: True,
    CONF_TRACK_SESSIONS: True,
}

# Categories stored in the log
CAT_SERVICE = "service"
CAT_PAGE_VIEW = "page_view"
CAT_LOGIN = "login"
CAT_LOGOUT = "logout"
CAT_SESSION = "session"
CAT_TOKEN = "token"
CAT_LOGIN_FAILED = "login_failed"
CAT_INDIRECT = "indirect"
CAT_EVENT = "event"

FLUSH_INTERVAL_SECONDS = 2
SESSION_POLL_SECONDS = 30
