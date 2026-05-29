// Multi-camera synchronized player. Front camera is the master clock; transport controls
// fan out to every tile and drift is corrected against the master. Multi-minute events are
// played as "scenes" — one minute at a time with a stepper (gapless concat is a later phase).
(function () {
  const DRIFT = 0.15;       // seconds of tolerated drift before a hard re-sync
  const POLL_MS = 800;
  const CAM_LABELS = {
    front: "Front", back: "Rear",
    left_repeater: "Left", right_repeater: "Right",
    left_pillar: "Left Pillar", right_pillar: "Right Pillar",
  };

  let detail = null;
  let minuteIndex = 0;
  let videos = {};          // camera -> <video>
  let master = null;        // master camera name
  let playing = false;
  let pollTimer = null;

  function vurl(camera, minuteTs) {
    return window.TUV.url(
      "/api/events/" + encodeURIComponent(detail.event_id) +
      "/video/" + encodeURIComponent(camera) + "/" + encodeURIComponent(minuteTs));
  }

  async function open(eventId) {
    stop();
    const view = document.getElementById("view");
    view.innerHTML = `<div class="preparing"><div class="spinner"></div><p id="prep-msg">Loading event…</p></div>`;
    try {
      detail = await window.TUV.api("/api/events/" + encodeURIComponent(eventId) + "/detail");
    } catch (e) {
      view.innerHTML = `<div class="empty"><p>Could not load event: ${e.message}</p>
        <p><a href="#/">← Back</a></p></div>`;
      return;
    }
    if (!detail.minutes.length) {
      view.innerHTML = `<div class="empty"><p>This event has no playable clips.</p>
        <p><a href="#/">← Back</a></p></div>`;
      return;
    }
    await prepareThenRender();
  }

  async function prepareThenRender() {
    const msg = document.getElementById("prep-msg");
    try {
      await window.TUV.api("/api/events/" + encodeURIComponent(detail.event_id) + "/prepare",
        { method: "POST" });
    } catch (e) {
      if (msg) msg.textContent = "Failed to prepare clips: " + e.message;
      return;
    }
    // Poll until the cache copy finishes.
    const poll = async () => {
      let st;
      try {
        st = await window.TUV.api("/api/events/" + encodeURIComponent(detail.event_id) + "/status");
      } catch (e) { st = { state: "error", error: e.message }; }
      if (st.state === "ready") { render(); return; }
      if (st.state === "error") {
        if (msg) msg.textContent = "Download failed: " + (st.error || "unknown error");
        return;
      }
      if (msg) msg.textContent = `Downloading clips from backend… (${st.ready || 0}/${st.total || "?"})`;
      pollTimer = setTimeout(poll, POLL_MS);
    };
    poll();
  }

  function render() {
    minuteIndex = 0;
    master = detail.cameras.includes("front") ? "front" : detail.cameras[0];
    const view = document.getElementById("view");
    view.innerHTML = `
      <div class="player">
        <div class="player-head">
          <a class="back" href="#/">← Back</a>
          <span class="player-title">${escAttr(detail.event_ts)}${detail.city ? " · " + escAttr(detail.city) : ""}</span>
        </div>
        <div class="cam-grid cams-${detail.cameras.length}" id="cam-grid"></div>
        <div class="transport">
          <button id="play-btn" title="Play/Pause">▶</button>
          <input type="range" id="seek" min="0" max="1000" value="0" step="1">
          <span id="time" class="time">0:00 / 0:00</span>
          <select id="rate" title="Playback speed">
            <option value="0.25">0.25×</option><option value="0.5">0.5×</option>
            <option value="1" selected>1×</option><option value="1.5">1.5×</option>
            <option value="2">2×</option>
          </select>
        </div>
        <div class="minutes" id="minutes"></div>
      </div>`;

    const grid = document.getElementById("cam-grid");
    videos = {};
    detail.cameras.forEach((cam) => {
      const tile = document.createElement("div");
      tile.className = "cam-tile";
      const v = document.createElement("video");
      v.playsInline = true;
      v.preload = "auto";
      v.muted = cam !== master; // a single audio source avoids echo
      v.addEventListener("error", () => showTileError(tile, cam));
      tile.appendChild(v);
      const label = document.createElement("span");
      label.className = "cam-label";
      label.textContent = CAM_LABELS[cam] || cam;
      tile.appendChild(label);
      grid.appendChild(tile);
      videos[cam] = v;
    });

    buildMinuteStepper();
    wireTransport();
    wireMasterSync();
    loadMinute(0);
  }

  function showTileError(tile, cam) {
    if (tile.querySelector(".cam-error")) return;
    const div = document.createElement("div");
    div.className = "cam-error";
    div.innerHTML = `<span>⚠️ Can't decode ${CAM_LABELS[cam] || cam}</span>
      <small>HW3+ records HEVC. Try Safari; transcoding is planned.</small>`;
    tile.appendChild(div);
  }

  function buildMinuteStepper() {
    const wrap = document.getElementById("minutes");
    if (detail.minutes.length <= 1) { wrap.style.display = "none"; return; }
    wrap.innerHTML = `<span class="minutes-label">Scene:</span>`;
    detail.minutes.forEach((m, i) => {
      const b = document.createElement("button");
      b.textContent = (i + 1);
      b.title = m;
      b.onclick = () => loadMinute(i);
      wrap.appendChild(b);
    });
  }

  function loadMinute(idx) {
    minuteIndex = idx;
    const minute = detail.minutes[idx];
    const cameras = minute.cameras; // camera -> filename for this minute
    Object.entries(videos).forEach(([cam, v]) => {
      const tile = v.closest(".cam-tile");
      const errEl = tile && tile.querySelector(".cam-error");
      if (errEl) errEl.remove();
      if (cameras[cam]) {
        v.src = vurl(cam, minute.minute_ts);
        v.load();
        v.style.visibility = "visible";
      } else {
        v.removeAttribute("src");
        v.style.visibility = "hidden";
      }
    });
    document.querySelectorAll("#minutes button").forEach((b, i) =>
      b.classList.toggle("active", i === idx));
    seekTo(0);
    if (playing) play();
  }

  function eachVideo(fn) { Object.values(videos).forEach(fn); }
  function masterVideo() { return videos[master]; }

  function wireTransport() {
    document.getElementById("play-btn").onclick = () => (playing ? pause() : play());
    document.getElementById("rate").onchange = (e) => {
      const r = parseFloat(e.target.value);
      eachVideo((v) => { v.playbackRate = r; });
    };
    const seek = document.getElementById("seek");
    seek.oninput = (e) => {
      const m = masterVideo();
      if (m && m.duration) seekTo((e.target.value / 1000) * m.duration);
    };
  }

  function wireMasterSync() {
    const m = masterVideo();
    if (!m) return;
    m.addEventListener("timeupdate", () => {
      // Re-sync any drifted camera and advance the seek bar / time label.
      eachVideo((v) => {
        if (v === m || !v.src || v.readyState < 1) return;
        if (Math.abs(v.currentTime - m.currentTime) > DRIFT) v.currentTime = m.currentTime;
      });
      const seek = document.getElementById("seek");
      const time = document.getElementById("time");
      if (m.duration) seek.value = Math.round((m.currentTime / m.duration) * 1000);
      time.textContent = fmt(m.currentTime) + " / " + fmt(m.duration || 0);
    });
    m.addEventListener("waiting", () => eachVideo((v) => { if (v !== m) v.pause(); }));
    m.addEventListener("playing", () => { if (playing) eachVideo((v) => { if (v !== m) v.play().catch(() => {}); }); });
    m.addEventListener("ended", () => {
      if (minuteIndex < detail.minutes.length - 1) loadMinute(minuteIndex + 1);
      else pause();
    });
  }

  function play() {
    playing = true;
    document.getElementById("play-btn").textContent = "⏸";
    eachVideo((v) => { if (v.src) v.play().catch(() => {}); });
  }
  function pause() {
    playing = false;
    document.getElementById("play-btn").textContent = "▶";
    eachVideo((v) => v.pause());
  }
  function seekTo(t) { eachVideo((v) => { if (v.src) v.currentTime = t; }); }

  function fmt(s) {
    if (!isFinite(s)) s = 0;
    const m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return m + ":" + String(sec).padStart(2, "0");
  }
  function escAttr(s) {
    return String(s == null ? "" : s).replace(/[<>&"]/g, (c) =>
      ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));
  }

  function stop() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    playing = false;
    eachVideo((v) => { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) {} });
    videos = {};
  }

  window.TUV = window.TUV || {};
  window.TUV.player = { open };
})();
