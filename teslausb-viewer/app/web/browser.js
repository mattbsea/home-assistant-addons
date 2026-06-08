// Event browser: paginated thumbnail grid for the selected folder + date filter.
(function () {
  const PAGE = 60;
  let offset = 0;
  let current = null;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtTimestamp(ts) {
    const d = new Date(ts);
    if (isNaN(d)) return ts;
    return d.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  function reasonLabel(reason) {
    if (!reason) return "";
    const map = {
      sentry_aware_object_detection: "Sentry: object",
      sentry_aware_motion_detection: "Sentry: motion",
      user_interaction_honk: "Honk",
      user_interaction_dashcam_icon_tapped: "Manual save",
    };
    if (map[reason]) return map[reason];
    if (reason.startsWith("sentry_aware_accel")) return "Sentry: impact";
    return reason.replace(/_/g, " ");
  }

  function card(ev) {
    const el = document.createElement("a");
    el.className = "card";
    el.href = "#/event/" + encodeURIComponent(ev.event_id);
    const thumbSrc = window.TUV.url("/api/events/" + encodeURIComponent(ev.event_id) + "/thumb");
    const meta = [reasonLabel(ev.reason), ev.city].filter(Boolean).join(" · ");
    // Always try the thumbnail (Tesla-supplied or ffmpeg-generated); fall back to a
    // placeholder if the server has none yet. Eager (no lazy) so the grid preloads.
    el.innerHTML = `
      <div class="card-thumb"><img src="${esc(thumbSrc)}" alt=""><span class="badge">${esc(ev.minute_count)}m · ${esc(ev.file_count)} clips</span></div>
      <div class="card-body">
        <div class="card-time">${esc(fmtTimestamp(ev.event_ts))}</div>
        <div class="card-meta">${meta ? esc(meta) : "&nbsp;"}</div>
      </div>`;
    el.querySelector("img").addEventListener("error", function () {
      const ph = document.createElement("div");
      ph.className = "thumb-placeholder";
      ph.textContent = "📹";
      this.replaceWith(ph);
    });
    return el;
  }

  async function show(state, append) {
    current = state;
    if (!append) offset = 0;
    const view = document.getElementById("view");
    if (!append) view.innerHTML = `<div class="grid" id="grid"></div><div id="grid-footer"></div>`;
    const grid = document.getElementById("grid");
    const footer = document.getElementById("grid-footer");
    footer.innerHTML = `<div class="loading">Loading…</div>`;

    // No folder → the "All" tab: omit the param so the API returns every folder.
    const params = new URLSearchParams({ limit: PAGE, offset });
    if (state.folder) params.set("folder", state.folder);
    if (state.date) {
      // event_ts is stored as ISO (YYYY-MM-DDTHH:MM:SS); bound the day in the same format.
      params.set("date_from", state.date + "T00:00:00");
      params.set("date_to", state.date + "T23:59:59");
    }
    try {
      const data = await window.TUV.api("/api/events?" + params.toString());
      if (!append && data.events.length === 0) {
        view.innerHTML = `<div class="empty">
          <p>No events found in <strong>${esc(state.folder || "any folder")}</strong>.</p>
          <p class="hint">If you just installed the add-on, hit <strong>↻ Refresh</strong> to scan your backend.</p></div>`;
        return;
      }
      data.events.forEach((ev) => grid.appendChild(card(ev)));
      offset += data.events.length;
      footer.innerHTML = "";
      if (offset < data.total) {
        const more = document.createElement("button");
        more.className = "load-more";
        more.textContent = `Load more (${offset}/${data.total})`;
        more.onclick = () => show(current, true);
        footer.appendChild(more);
      }
    } catch (e) {
      footer.innerHTML = "";
      window.TUV.status("Failed to load events: " + e.message, true);
    }
  }

  window.TUV = window.TUV || {};
  window.TUV.browser = { show };
  window.TUV.reasonLabel = reasonLabel;   // reused by the player's metadata overlay
})();
