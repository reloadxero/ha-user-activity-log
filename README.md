# User Activity Log for Home Assistant

See **who** used Home Assistant, **when**, and **what they did**: switches flipped, input booleans toggled, scripts run, dashboards opened, logins and failed logins, all in one **Activity Log** sidebar panel.

## What gets logged

| Type | What it records | How |
|---|---|---|
| **Action** | Every action (service call) a user makes, e.g. `switch.turn_on`, `input_boolean.toggle`, `light.turn_on` with brightness, with the target entity and its friendly name | Event bus, `context.user_id` |
| **Page view** | Every dashboard, view or page a user opens (`/lovelace/kitchen`, `/config/devices`…), plus "opened app" and "returned to app" markers, client name and IP | A small JS module the integration adds to the frontend |
| **Via automation** | Automations, scripts and actions that a user's action set off (e.g. a user flips a switch → automation "Hallway lights" runs) | Context parent chain |
| **Login / Logout** | New login sessions, with client and IP; removed sessions | Polls refresh tokens every 30 s |
| **Session** | When a logged-in device refreshed its session (roughly: last active) | Same as above, can be turned off |
| **Token** | Long-lived access tokens created or removed | Same as above |
| **Failed login** | Failed logins and requests with invalid authentication, with IP | `http.ban` warnings |
| **Event** | Any other event fired with a user context | Event bus |

Secrets in action data (`code`, `password`, `pin`, `token`…) are redacted before being stored.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/reloadxero/ha-user-activity-log`, category **Integration**.
2. Install **User Activity Log** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → User Activity Log**.
4. Reload open browser tabs and apps so the page-view tracker is loaded.

### Manual

Copy `custom_components/user_activity_log` into your `/config/custom_components/`, restart, then add the integration as above.

## Options

**Settings → Devices & services → User Activity Log → Configure**

- **Keep history for (days)**: default 30.
- **Log dashboard and page views**
- **Log automations and scripts triggered by a user's action**
- **Log session refreshes**

## The panel

Admins get an **Activity Log** entry in the sidebar. You can filter by user, type, time range and free text. New entries appear live. Click a row to see the full raw record, or use **Export CSV**.

Data is stored in `/config/user_activity_log.db` (SQLite). It is separate from the recorder database and is included in Home Assistant backups.

## Limits

- **Page views are reported by the browser.** They are logged for every browser and the Companion app once the page has been reloaded after install. A technical user could block the script, so treat this as an activity log, not tamper-proof auditing.
- **Automations have no user.** They show up under a user only when that user's action triggered them (see *Via automation*).
- **Actions from integrations using a long-lived token** (Node-RED, AppDaemon…) are logged under the token's owner.
- **Session times are approximate.** Login and session times come from token polling, so they can be up to 30 s off; IPs are the token's last-used IP.
- **Failed logins depend on log level.** They are captured from the `homeassistant.components.http.ban` logger, so they are missed if that logger is set above `warning`.
