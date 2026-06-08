// Multi-camera synchronized player. Front camera is the master clock; transport controls
// fan out to every tile and drift is corrected against the master. Multi-minute events are
// played as "scenes" — one minute at a time with a stepper (gapless concat is a later phase).
(function () {
  const DRIFT = 0.15;       // seconds of tolerated drift before a hard re-sync
  const CAM_LABELS = {
    front: "Front", back: "Rear",
    left_repeater: "Left", right_repeater: "Right",
    left_pillar: "Left Pillar", right_pillar: "Right Pillar",
  };
  // Fixed 3×2 layout: pillars flank the front up top, repeaters flank the rear below.
  // Cameras absent from an event leave their slot blank (front stays centred) — the
  // grid-area names below are matched in style.css's .cam-grid template.
  const CAM_SLOTS = ["left_pillar", "front", "right_pillar", "left_repeater", "back", "right_repeater"];
  const CAM_AREA = {
    left_pillar: "lp", front: "f", right_pillar: "rp",
    left_repeater: "l", back: "r", right_repeater: "rr",
  };

  let detail = null;
  let minuteIndex = 0;
  let videos = {};          // camera -> <video>
  let master = null;        // master camera name
  let playing = false;

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
    // Clips stream on demand — render immediately and let each <video> pull from /video.
    render();
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
        <div class="cam-grid" id="cam-grid"></div>
        <div class="transport">
          <button id="play-btn" title="Play/Pause">▶</button>
          <input type="range" id="seek" min="0" max="1000" value="0" step="1">
          <span id="time" class="time">0:00 / 0:00</span>
          <select id="rate" title="Playback speed">
            <option value="0.25">0.25×</option><option value="0.5">0.5×</option>
            <option value="1" selected>1×</option><option value="1.5">1.5×</option>
            <option value="2">2×</option>
          </select>
          <button id="meta-toggle" title="Show/hide info overlay">ⓘ</button>
        </div>
        <div class="minutes" id="minutes"></div>
      </div>`;

    const grid = document.getElementById("cam-grid");
    videos = {};
    CAM_SLOTS.forEach((cam) => {
      const tile = document.createElement("div");
      tile.className = "cam-tile";
      tile.style.gridArea = CAM_AREA[cam];
      if (!detail.cameras.includes(cam)) {
        tile.classList.add("empty"); // fixed slot, no footage this event — left blank
        grid.appendChild(tile);
        return;
      }
      const v = document.createElement("video");
      v.playsInline = true;
      v.preload = "auto";
      // Tesla clips have no audio track, so mute every tile. Muted playback is the one
      // case browsers allow to autoplay without a user gesture; an unmuted element gates
      // play() behind a gesture (the muted *flag* is checked, not whether real audio
      // exists) — which is exactly what silently broke auto-play-on-open.
      v.muted = true;
      v.addEventListener("error", () => showTileError(tile, cam));
      tile.appendChild(v);
      const label = document.createElement("span");
      label.className = "cam-label";
      label.textContent = CAM_LABELS[cam] || cam;
      tile.appendChild(label);
      grid.appendChild(tile);
      videos[cam] = v;
    });

    buildMetaOverlay();
    buildMinuteStepper();
    wireTransport();
    wireMasterSync();
    // Auto-play: opening an event is a deliberate "watch this" gesture, so start
    // rolling immediately. loadMinute() honours this once the master can play.
    playing = true;
    document.getElementById("play-btn").textContent = "⏸";
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

  // --- metadata overlay: live recording clock + per-event metadata --------------
  // A translucent strip across the grid bottom. The clock ticks (clip start time, parsed
  // from the filename, + the master's currentTime); the static line shows trigger reason,
  // city, and a map link — only the fields the event actually has (RecentClips have none).
  function buildMetaOverlay() {
    const grid = document.getElementById("cam-grid");
    if (!grid) return;
    const ov = document.createElement("div");
    ov.className = "meta-overlay";
    ov.id = "meta-overlay";
    ov.innerHTML = `<span class="meta-clock" id="meta-clock"></span>` +
      `<span class="meta-info">${buildMetaInfo()}</span>`;
    grid.appendChild(ov);
    if (metaHidden()) { ov.classList.add("hidden"); }
    updateMetaClock();
  }

  function buildMetaInfo() {
    const parts = [];
    const label = window.TUV.reasonLabel ? window.TUV.reasonLabel(detail.reason) : detail.reason;
    if (label) parts.push(`<span>${escAttr(label)}</span>`);
    if (detail.city) parts.push(`<span>${escAttr(detail.city)}</span>`);
    if (detail.est_lat != null && detail.est_lon != null) {
      const q = encodeURIComponent(`${detail.est_lat},${detail.est_lon}`);
      parts.push(`<a href="https://www.google.com/maps?q=${q}" target="_blank" rel="noopener">📍 map</a>`);
    }
    return parts.join(`<span class="sep">·</span>`);
  }

  // "YYYY-MM-DD_HH-MM-SS" -> a Date built from the literal wall-clock components (the time
  // is shown back in the same local frame, so this is timezone-neutral — no conversion).
  function clipStartDate() {
    const m = detail.minutes[minuteIndex];
    if (!m || !m.minute_ts) return null;
    const [d, t] = m.minute_ts.split("_");
    if (!d || !t) return null;
    const [Y, Mo, D] = d.split("-").map(Number);
    const [H, Mi, S] = t.split("-").map(Number);
    const dt = new Date(Y, Mo - 1, D, H, Mi, S);
    return isNaN(dt) ? null : dt;
  }

  function updateMetaClock() {
    const el = document.getElementById("meta-clock");
    if (!el) return;
    const base = clipStartDate();
    if (!base) { el.textContent = ""; return; }
    const mv = masterVideo();
    const offset = mv && isFinite(mv.currentTime) ? mv.currentTime : 0;
    const now = new Date(base.getTime() + offset * 1000);
    const date = now.toLocaleDateString(undefined,
      { weekday: "short", month: "short", day: "numeric", year: "numeric" });
    const time = now.toLocaleTimeString(undefined,
      { hour: "numeric", minute: "2-digit", second: "2-digit" });
    el.textContent = `${date} · ${time}`;
  }

  function metaHidden() {
    try { return localStorage.getItem("tuv_meta_hidden") === "1"; } catch (e) { return false; }
  }
  function setMetaHidden(hidden) {
    try { localStorage.setItem("tuv_meta_hidden", hidden ? "1" : "0"); } catch (e) { /* ignore */ }
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

    // Reset the transport UI now. The seek bar / time label are otherwise only
    // updated from the master's "timeupdate", which won't fire until playback
    // actually resumes — and never fires if the scene is switched while paused.
    const seek = document.getElementById("seek");
    const time = document.getElementById("time");
    if (seek) seek.value = 0;
    if (time) time.textContent = "0:00 / 0:00";
    updateMetaClock();   // reflect the new scene's start time even while paused

    // Safari (and others) silently drop a currentTime/play() issued while the
    // freshly load()ed media is still at readyState 0, so the new scene never
    // starts — the picture freezes and the scrubber stays stuck. Wait until the
    // master can actually play, then seek to 0 and (re)start.
    const m = masterVideo();
    const start = () => { seekTo(0); if (playing) play(); };
    if (m && m.src && m.readyState < 2) {
      m.addEventListener("canplay", start, { once: true });
    } else {
      start();
    }
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
    const mt = document.getElementById("meta-toggle");
    if (mt) {
      mt.classList.toggle("active", !metaHidden());
      mt.onclick = () => {
        const ov = document.getElementById("meta-overlay");
        if (!ov) return;
        const hidden = ov.classList.toggle("hidden");
        setMetaHidden(hidden);
        mt.classList.toggle("active", !hidden);
      };
    }
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
      updateMetaClock();
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
    // Tesla clips carry no audio track, so browsers allow autoplay without a user
    // gesture — the auto-play on open (and scene-advance) just rolls. .catch() swallows
    // the benign abort if a scene is switched out from under a pending play().
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
    playing = false;
    eachVideo((v) => { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) {} });
    videos = {};
  }

  window.TUV = window.TUV || {};
  window.TUV.player = { open };
})();
