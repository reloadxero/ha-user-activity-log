// User Activity Log - frontend page view tracker.
// Loaded on every Home Assistant frontend page; reports which page the
// logged-in user is looking at to the user_activity_log WebSocket API.
(() => {
  if (window.__userActivityLogTracker) return;
  window.__userActivityLogTracker = true;

  const MESSAGE_TYPE = "user_activity_log/page_view";
  const SETTLE_MS = 1000; // ignore redirects like /lovelace -> /lovelace/0
  const RESUME_AFTER_MS = 10 * 60 * 1000; // tab hidden this long counts as a new visit

  let lastPath = null;
  let pending = null;
  let initialSent = false;
  let hiddenAt = null;
  let disabled = false;

  const getHass = () => document.querySelector("home-assistant")?.hass;

  const panelTitle = (hass, path) => {
    const segment = path.split("/")[1] || "";
    const panel = hass.panels?.[segment];
    if (!panel) return null;
    if (panel.title) return panel.title;
    return panel.component_name === "lovelace" ? "Overview" : segment;
  };

  const send = async (hass, path, extra) => {
    if (disabled) return;
    try {
      await hass.connection.sendMessagePromise({
        type: MESSAGE_TYPE,
        path,
        title: panelTitle(hass, path),
        ...extra,
      });
    } catch (err) {
      // Integration removed or not loaded: stop trying until the next reload.
      if (err?.code === "unknown_command") disabled = true;
    }
  };

  const schedule = () => {
    clearTimeout(pending);
    pending = setTimeout(() => {
      const path = location.pathname;
      if (path === lastPath) return;
      const hass = getHass();
      if (!hass?.connection || !hass.user) {
        schedule(); // frontend still starting up
        return;
      }
      lastPath = path;
      const extra = {};
      if (!initialSent) {
        initialSent = true;
        extra.initial = true;
        extra.user_agent = navigator.userAgent;
      }
      send(hass, path, extra);
    }, SETTLE_MS);
  };

  window.addEventListener("location-changed", schedule);
  window.addEventListener("popstate", schedule);
  setInterval(() => {
    if (location.pathname !== lastPath) schedule();
  }, 3000);

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      hiddenAt = Date.now();
      return;
    }
    if (hiddenAt && Date.now() - hiddenAt > RESUME_AFTER_MS) {
      const hass = getHass();
      if (hass?.connection) send(hass, location.pathname, { resumed: true });
    }
    hiddenAt = null;
  });

  schedule();
})();
