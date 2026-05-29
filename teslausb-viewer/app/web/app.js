// Core app: URL helper, API client, hash router, status bar. Exposes window.TUV.
(function () {
  const BASE = window.INGRESS_BASE || "";

  // Build an absolute URL from the ingress base. NEVER use bare "/..." (404s under ingress).
  function url(path) {
    if (!path.startsWith("/")) path = "/" + path;
    return BASE + path;
  }

  async function api(path, opts) {
    const res = await fetch(url(path), opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    const ct = res.headers.get("content-type") || "";
    return ct.includes("application/json") ? res.json() : res;
  }

  function status(msg, isError) {
    const bar = document.getElementById("status-bar");
    if (!msg) { bar.textContent = ""; bar.className = "status-bar"; return; }
    bar.textContent = msg;
    bar.className = "status-bar show" + (isError ? " error" : "");
  }

  // --- routing: #/ (browser) and #/event/<id> (player) ----------------------
  const state = { folder: "SavedClips", date: "" };

  function route() {
    const hash = location.hash || "#/";
    if (hash.startsWith("#/event/")) {
      const id = decodeURIComponent(hash.slice("#/event/".length));
      window.TUV.player.open(id);
    } else {
      window.TUV.browser.show(state);
    }
  }

  function navigate(hash) { location.hash = hash; }

  function init() {
    document.getElementById("folder-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-folder]");
      if (!btn) return;
      state.folder = btn.dataset.folder;
      document.querySelectorAll("#folder-tabs button").forEach((b) =>
        b.classList.toggle("active", b === btn));
      navigate("#/");
      window.TUV.browser.show(state);
    });

    document.getElementById("date-filter").addEventListener("change", (e) => {
      state.date = e.target.value;
      window.TUV.browser.show(state);
    });

    document.getElementById("refresh-btn").addEventListener("click", async () => {
      status("Refreshing index…");
      try {
        const r = await api("/api/refresh", { method: "POST" });
        status(r.skipped ? "No backend configured" : `Indexed ${r.added} new event(s)`);
        window.TUV.browser.show(state);
      } catch (e) {
        status("Refresh failed: " + e.message, true);
      }
      setTimeout(() => status(""), 4000);
    });

    window.addEventListener("hashchange", route);
    route();
  }

  window.TUV = { url, api, status, navigate, state, init };
})();
