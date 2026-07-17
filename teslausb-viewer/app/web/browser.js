// Event browser: paginated thumbnail grid for the selected folder + date filter, plus
// multi-select delete (toggled via the topbar "Select" button in app.js).
(function () {
  const PAGE = 60;
  let offset = 0;
  let current = null;
  let selectMode = false;
  let selected = new Set();

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
    el.dataset.eventId = ev.event_id;
    const thumbSrc = window.TUV.url("/api/events/" + encodeURIComponent(ev.event_id) + "/thumb");
    const meta = [reasonLabel(ev.reason), ev.city].filter(Boolean).join(" · ");
    // Always try the thumbnail (Tesla-supplied or ffmpeg-generated); fall back to a
    // placeholder if the server has none yet. Eager (no lazy) so the grid preloads.
    // The select checkbox is always in the DOM (hidden by CSS unless .grid.selecting) so
    // toggling select mode doesn't require re-rendering already-loaded cards.
    el.innerHTML = `
      <div class="card-thumb">
        <img src="${esc(thumbSrc)}" alt="">
        <span class="card-select"><input type="checkbox" tabindex="-1" aria-hidden="true"></span>
        <span class="badge">${esc(ev.minute_count)}m · ${esc(ev.file_count)} clips</span>
      </div>
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

  // --- multi-select -----------------------------------------------------

  function onGridClick(e) {
    if (!selectMode) return;
    const el = e.target.closest(".card");
    if (!el) return;
    e.preventDefault();
    const id = el.dataset.eventId;
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    el.classList.toggle("selected", selected.has(id));
    const cb = el.querySelector(".card-select input");
    if (cb) cb.checked = selected.has(id);
    renderSelectBar();
  }

  function setSelectMode(on) {
    selectMode = on;
    const grid = document.getElementById("grid");
    const btn = document.getElementById("select-btn");
    if (btn) btn.classList.toggle("active", selectMode);
    if (grid) grid.classList.toggle("selecting", selectMode);
    if (!selectMode) {
      selected.clear();
      if (grid) {
        grid.querySelectorAll(".card.selected").forEach((c) => c.classList.remove("selected"));
        grid.querySelectorAll(".card-select input").forEach((cb) => (cb.checked = false));
      }
    }
    renderSelectBar();
  }

  function toggleSelectMode() { setSelectMode(!selectMode); }
  function exitSelectMode() { setSelectMode(false); }

  function selectAllLoaded() {
    const grid = document.getElementById("grid");
    if (!grid) return;
    grid.querySelectorAll(".card").forEach((el) => {
      selected.add(el.dataset.eventId);
      el.classList.add("selected");
      const cb = el.querySelector(".card-select input");
      if (cb) cb.checked = true;
    });
    renderSelectBar();
  }

  function renderSelectBar() {
    const bar = document.getElementById("select-bar");
    if (!selectMode) {
      bar.className = "select-bar";
      bar.innerHTML = "";
      return;
    }
    const grid = document.getElementById("grid");
    const loadedCount = grid ? grid.children.length : 0;
    bar.className = "select-bar show";
    bar.innerHTML = `
      <span class="select-count">${selected.size} selected</span>
      <button id="select-all-btn">Select all (${loadedCount})</button>
      <button id="delete-selected-btn" ${selected.size ? "" : "disabled"}>Delete selected</button>
      <button id="select-cancel-btn">Cancel</button>`;
    bar.querySelector("#select-all-btn").onclick = selectAllLoaded;
    bar.querySelector("#delete-selected-btn").onclick = deleteSelected;
    bar.querySelector("#select-cancel-btn").onclick = exitSelectMode;
  }

  async function deleteSelected() {
    if (selected.size === 0) return;
    const ids = Array.from(selected);
    const ok = confirm(
      `Delete ${ids.length} event(s)? This permanently removes the video files from disk.`
    );
    if (!ok) return;
    window.TUV.status(`Deleting ${ids.length} event(s)…`);
    let result;
    try {
      result = await window.TUV.api("/api/events/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_ids: ids }),
      });
    } catch (e) {
      window.TUV.status("Delete failed: " + e.message, true);
      return;
    }
    const grid = document.getElementById("grid");
    const deletedIds = result.deleted || [];
    const failed = result.failed || [];
    if (grid) {
      grid.querySelectorAll(".card").forEach((el) => {
        if (deletedIds.includes(el.dataset.eventId)) el.remove();
      });
    }
    deletedIds.forEach((id) => selected.delete(id));
    window.TUV.status(
      failed.length
        ? `Deleted ${deletedIds.length} of ${ids.length} event(s) (${failed.length} failed)`
        : `Deleted ${deletedIds.length} event(s)`,
      failed.length > 0
    );
    // Failed ids stay selected so the user can see and retry them; only exit select mode
    // once nothing is left to retry.
    if (failed.length === 0) exitSelectMode();
    else renderSelectBar();
  }

  // --- grid rendering -----------------------------------------------------

  async function show(state, append) {
    current = state;
    if (!append) offset = 0;
    const view = document.getElementById("view");
    if (!append) {
      view.innerHTML = `<div class="grid" id="grid"></div><div id="grid-footer"></div>`;
      selected.clear();
      const grid = document.getElementById("grid");
      grid.classList.toggle("selecting", selectMode);
      grid.addEventListener("click", onGridClick);
    }
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
        renderSelectBar();
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
      renderSelectBar();
    } catch (e) {
      footer.innerHTML = "";
      window.TUV.status("Failed to load events: " + e.message, true);
    }
  }

  window.TUV = window.TUV || {};
  window.TUV.browser = { show, toggleSelectMode, exitSelectMode };
  window.TUV.reasonLabel = reasonLabel;   // reused by the player's metadata overlay
})();
