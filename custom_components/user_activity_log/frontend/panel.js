// User Activity Log - sidebar panel.
const CATEGORIES = {
  service: { label: "Action", color: "#2b7de9" },
  page_view: { label: "Page view", color: "#8e44ad" },
  login: { label: "Login", color: "#27ae60" },
  logout: { label: "Logout", color: "#7f8c8d" },
  session: { label: "Session", color: "#16a085" },
  token: { label: "Token", color: "#d35400" },
  login_failed: { label: "Failed login", color: "#c0392b" },
  indirect: { label: "Via automation", color: "#f39c12" },
  event: { label: "Event", color: "#34495e" },
};

const RANGES = {
  "1h": 3600,
  "24h": 86400,
  "7d": 7 * 86400,
  "30d": 30 * 86400,
  all: null,
};

const PAGE_SIZE = 200;

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

const summarize = (row) => {
  const d = row.details || {};
  switch (row.category) {
    case "service":
    case "indirect": {
      const names = d.names ? Object.values(d.names).join(", ") : "";
      const data = { ...(d.data || {}) };
      for (const key of ["entity_id", "device_id", "area_id", "floor_id", "label_id"]) delete data[key];
      const extra = Object.keys(data).length ? JSON.stringify(data) : "";
      return [names, extra].filter(Boolean).join(" · ");
    }
    case "page_view": {
      const bits = [d.title];
      if (d.opened) bits.push("opened app");
      if (d.resumed) bits.push("returned to app");
      if (d.client) bits.push(d.client);
      if (d.ip) bits.push(d.ip);
      return bits.filter(Boolean).join(" · ");
    }
    case "login":
    case "logout":
    case "session":
    case "token":
    case "login_failed":
      return [d.client, d.ip].filter(Boolean).join(" · ");
    default:
      return d.data ? JSON.stringify(d.data) : "";
  }
};

class UserActivityLogPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._events = [];
    this._users = [];
    this._hasMore = false;
    this._loading = false;
    this._live = true;
    this._unsub = null;
    this._filters = { user_id: "", category: "", search: "", range: "24h" };
    this._searchTimer = null;
    this._expanded = new Set();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (first) {
      this._renderShell();
      this._load(true);
      this._subscribe();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
  }

  disconnectedCallback() {
    this._unsubscribe();
  }

  _unsubscribe() {
    if (this._unsub) {
      this._unsub.then((unsub) => unsub()).catch(() => {});
      this._unsub = null;
    }
  }

  _subscribe() {
    if (!this._live || this._unsub || !this._hass) return;
    this._unsub = this._hass.connection
      .subscribeMessage((msg) => this._onLive(msg.events || []), {
        type: "user_activity_log/subscribe",
      })
      .catch((err) => {
        this._unsub = null;
        this._showError(err);
      });
  }

  _matches(row) {
    const f = this._filters;
    if (f.user_id && row.user_id !== f.user_id) return false;
    if (f.category && row.category !== f.category) return false;
    if (f.search) {
      const hay = [row.action, row.entity_id, row.user_name, JSON.stringify(row.details || {})]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(f.search.toLowerCase())) return false;
    }
    return true;
  }

  _onLive(rows) {
    const fresh = rows.filter((r) => this._matches(r)).reverse();
    if (!fresh.length) return;
    const known = new Set(this._events.map((e) => e.id));
    this._events = [...fresh.filter((r) => !known.has(r.id)), ...this._events];
    for (const row of rows) {
      if (row.user_id && !this._users.some((u) => u.user_id === row.user_id)) {
        this._users.push({ user_id: row.user_id, user_name: row.user_name });
        this._renderUsers();
      }
    }
    this._renderRows();
  }

  async _load(reset) {
    if (this._loading) return;
    this._loading = true;
    this._setStatus("Loading…");
    const f = this._filters;
    const msg = { type: "user_activity_log/list", limit: PAGE_SIZE };
    if (f.user_id) msg.user_id = f.user_id;
    if (f.category) msg.category = f.category;
    if (f.search) msg.search = f.search;
    if (RANGES[f.range]) msg.start_ts = Date.now() / 1000 - RANGES[f.range];
    if (!reset && this._events.length) msg.before_id = this._events[this._events.length - 1].id;
    try {
      const result = await this._hass.callWS(msg);
      this._events = reset ? result.events : [...this._events, ...result.events];
      this._users = result.users;
      this._hasMore = result.has_more;
      this._renderUsers();
      this._renderRows();
    } catch (err) {
      this._showError(err);
    } finally {
      this._loading = false;
    }
  }

  _showError(err) {
    this._setStatus(`Error: ${err?.message || err?.code || err}`);
  }

  _setStatus(text) {
    const el = this.shadowRoot.getElementById("status");
    if (el) el.textContent = text;
  }

  _renderShell() {
    const categoryOptions = Object.entries(CATEGORIES)
      .map(([key, c]) => `<option value="${key}">${c.label}</option>`)
      .join("");
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; height: 100%; background: var(--primary-background-color); color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
        .toolbar { display: flex; align-items: center; height: var(--header-height, 56px); padding: 0 12px; background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, white); font-size: 20px; gap: 8px; box-sizing: border-box; }
        .content { padding: 16px; box-sizing: border-box; height: calc(100% - var(--header-height, 56px)); overflow: auto; }
        .filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
        select, input[type=search], button { font: inherit; font-size: 14px; padding: 6px 10px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
        input[type=search] { min-width: 200px; flex: 1; }
        button { cursor: pointer; }
        button.primary { background: var(--primary-color); color: var(--text-primary-color, white); border-color: var(--primary-color); }
        label.live { display: flex; align-items: center; gap: 4px; font-size: 14px; }
        .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); border: 1px solid var(--divider-color); overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
        th { font-weight: 500; color: var(--secondary-text-color); position: sticky; top: 0; background: var(--card-background-color); }
        tr.row { cursor: pointer; }
        tr.row:hover { background: var(--secondary-background-color); }
        td.time { white-space: nowrap; color: var(--secondary-text-color); }
        td.user { white-space: nowrap; font-weight: 500; }
        td.summary { color: var(--secondary-text-color); max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; color: white; font-size: 12px; white-space: nowrap; }
        code { font-size: 13px; word-break: break-all; }
        pre { margin: 0; white-space: pre-wrap; word-break: break-all; font-size: 12px; background: var(--secondary-background-color); padding: 8px; border-radius: 6px; }
        .footer { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; color: var(--secondary-text-color); font-size: 14px; }
        .empty { padding: 32px; text-align: center; color: var(--secondary-text-color); }
      </style>
      <div class="toolbar"><ha-menu-button id="menu"></ha-menu-button><span>Activity Log</span></div>
      <div class="content">
        <div class="filters">
          <select id="user"><option value="">All users</option></select>
          <select id="category"><option value="">All types</option>${categoryOptions}</select>
          <select id="range">
            <option value="1h">Last hour</option>
            <option value="24h" selected>Last 24 hours</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="all">All time</option>
          </select>
          <input id="search" type="search" placeholder="Search entity, action, page…">
          <label class="live"><input id="live" type="checkbox" checked> Live</label>
          <button id="refresh">Refresh</button>
          <button id="export">Export CSV</button>
        </div>
        <div class="card">
          <table>
            <thead><tr><th>Time</th><th>User</th><th>Type</th><th>What</th><th>Target</th><th>Details</th></tr></thead>
            <tbody id="rows"></tbody>
          </table>
        </div>
        <div class="footer"><span id="status"></span><button id="more" class="primary" hidden>Load more</button></div>
      </div>`;

    const $ = (id) => this.shadowRoot.getElementById(id);
    this._menu = $("menu");
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;

    const refilter = () => this._load(true);
    $("user").addEventListener("change", (e) => { this._filters.user_id = e.target.value; refilter(); });
    $("category").addEventListener("change", (e) => { this._filters.category = e.target.value; refilter(); });
    $("range").addEventListener("change", (e) => { this._filters.range = e.target.value; refilter(); });
    $("search").addEventListener("input", (e) => {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => { this._filters.search = e.target.value.trim(); refilter(); }, 400);
    });
    $("live").addEventListener("change", (e) => {
      this._live = e.target.checked;
      if (this._live) { this._subscribe(); refilter(); } else this._unsubscribe();
    });
    $("refresh").addEventListener("click", refilter);
    $("more").addEventListener("click", () => this._load(false));
    $("export").addEventListener("click", () => this._exportCsv());
    $("rows").addEventListener("click", (e) => {
      const tr = e.target.closest("tr.row");
      if (!tr) return;
      const id = Number(tr.dataset.id);
      if (this._expanded.has(id)) this._expanded.delete(id);
      else this._expanded.add(id);
      this._renderRows();
    });
  }

  _renderUsers() {
    const select = this.shadowRoot.getElementById("user");
    if (!select) return;
    const current = this._filters.user_id;
    select.innerHTML =
      `<option value="">All users</option>` +
      this._users
        .map((u) => `<option value="${esc(u.user_id)}"${u.user_id === current ? " selected" : ""}>${esc(u.user_name || u.user_id)}</option>`)
        .join("");
  }

  _renderRows() {
    const tbody = this.shadowRoot.getElementById("rows");
    if (!tbody) return;
    if (!this._events.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty">No activity for these filters yet.</td></tr>`;
    } else {
      tbody.innerHTML = this._events
        .map((row) => {
          const cat = CATEGORIES[row.category] || { label: row.category, color: "#555" };
          const time = new Date(row.ts * 1000).toLocaleString();
          const main = `<tr class="row" data-id="${row.id}">
            <td class="time">${esc(time)}</td>
            <td class="user">${esc(row.user_name || (row.user_id ? row.user_id.slice(0, 8) : "—"))}</td>
            <td><span class="badge" style="background:${cat.color}">${esc(cat.label)}</span></td>
            <td><code>${esc(row.action)}</code></td>
            <td><code>${esc(row.entity_id || "")}</code></td>
            <td class="summary" title="${esc(summarize(row))}">${esc(summarize(row))}</td>
          </tr>`;
          if (!this._expanded.has(row.id)) return main;
          return `${main}<tr><td colspan="6"><pre>${esc(JSON.stringify(row, null, 2))}</pre></td></tr>`;
        })
        .join("");
    }
    const more = this.shadowRoot.getElementById("more");
    if (more) more.hidden = !this._hasMore;
    this._setStatus(`${this._events.length} entries`);
  }

  _exportCsv() {
    const header = ["time", "user", "type", "action", "target", "details"];
    const quote = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [header.join(",")].concat(
      this._events.map((r) =>
        [
          new Date(r.ts * 1000).toISOString(),
          r.user_name || r.user_id || "",
          r.category,
          r.action,
          r.entity_id || "",
          JSON.stringify(r.details || {}),
        ]
          .map(quote)
          .join(",")
      )
    );
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `activity-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
}

if (!customElements.get("user-activity-log-panel")) {
  customElements.define("user-activity-log-panel", UserActivityLogPanel);
}
