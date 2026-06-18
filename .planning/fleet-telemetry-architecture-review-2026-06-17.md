# Fleet Telemetry Add-on — Architectural Review
_2026-06-17 · scope: all processes, servers, and code in `fleet-telemetry/`_

## 1. What actually runs (process & data-flow inventory)

`run.sh` is a bash supervisor. Steady-state processes once fully configured:

```
                    Tesla vehicle ──mTLS──▶ fleet-telemetry binary (Go)   :4443 telemetry / :8080 status
                                                   │ stdout
                                                   ▼
                                                 tee -a ──▶ /tmp/ft-records.jsonl   (the "bus")
                                                              │      │      │
                            ┌─────────────────────────────────┘      │      └────────────────────┐
                            ▼ tail+parse                              ▼ tail+parse                ▼ tail+parse
                     server.py (Python)                        shim.py (Python)            bridge.py (Python)
                     web/wizard/dashboard :8099                Fleet-API REST shim :8085   POST→ :8081
                     pubkey host :8100                         (TeslaMate polling)              │ Pub/Sub envelope
                            │                                                                   ▼
                            └─ on-demand: tesla-http-proxy (Go, signs send-config)      teslamate-ws (Node) :8081
                                                                                        ws /streaming/ → TeslaMate
```

- **Runtimes in the image:** Go (2 prebuilt binaries: fleet-telemetry, tesla-http-proxy), **Python 3** (server/shim/bridge), **Node 20** (teslamate-ws only).
- **Long-running process count:** default (bridge off) = bash + Go binary + `tee` + **2 Python**. With streaming on = + **bridge.py** + **Node**. `tesla-http-proxy` is spawned on demand during "Send to Vehicle" only.
- **IPC:** everything is glued through one append-only JSONL file in `/tmp`, written by `tee` and independently tailed by each consumer.

## 2. What's working well (preserve these)

- **Wizard-first / never-fatal inversion** (`run.sh` 9–13, `set +e…` at 7): the web UI starts first and never fatals, so a fresh install is always reachable. This fixed a real bug — do not regress it.
- **Config file as the restart signal** (`reconcile()` on `wizard-config.json` hash change): clean, no add-on restart needed.
- **Deferred cert/binary start**: telemetry server waits for a cert instead of crash-looping.
- **Reusing Tesla's binary + `tesla-http-proxy`** instead of reimplementing mTLS / JWS signing: correct call.
- **Bridge gated behind `bridge_enabled`**: optional cost stays opt-in.

## 3. Duplication & streamlining opportunities (prioritized)

### Tier 1 — pure dedup, no behavior change (do first; lowest risk)

**A. One records-tail helper.** `server.py:_tail_records`, `shim.py:_tail`, `bridge.py:tail` are three near-identical copies of the same follow-with-rotation loop (~25 lines each, subtly divergent). Extract one `records.tail(callback)` generator imported by all consumers. Removes the bulk of the fragile IPC code and the 2× (default) / 3× (streaming) redundant JSON parse of every line.

**B. One field-knowledge module.** Telemetry field/shape logic is smeared across 4 files:
- `_META` set is defined **twice** (`server.py`, `shim.py`) and partially a third time (`bridge.py`).
- Enum stripping (`DetailedChargeStateDisconnected → …`) exists 3×: `server.py:pretty`, `shim.py:_strip_state`, `index.js` inline.
- Gear/`ShiftState` normalization 3×: `bridge.py:_gear_letter`, `index.js .replace("ShiftState","")`, shim.
- Location parsing 4×: `server.py:_parse_location`, `bridge.py`, `index.js`, shim.
- **Inverse-map smell:** `server.py:_prime_to_fields` (vehicle_data → telemetry names) and `shim.py:_assemble` (telemetry names → vehicle_data) are hand-maintained **inverse mappings of the same correspondence table**. Replace with one bidirectional table.

  Consolidate into a shared `fields.py`: the field roster, `_META`, enum-strip, gear map, location parse, and the bidirectional telemetry↔vehicle_data table.

**C. One NPM client.** NPM API logic is duplicated across languages: `scripts/fetch-npm-cert.sh` (117 lines bash/curl: `POST /api/tokens` + cert list/download) and run.sh `fetch_cert`, vs `server.py`'s Python NPM client (`_npm_token`/cert/host/stream — its own comment admits it "mirrors scripts/fetch-npm-cert.sh auth"). Let `server.py` own cert fetch (it already authenticates and creates the cert/host/stream), write `server.crt/key`, and **delete the bash script + `fetch_cert` plumbing**. `run.sh` already waits for the cert file to appear, so this fits the reactive model.

**D. Derive the dashboard field set.** `server.py:1541` hardcodes an ~80-name `grouped` JS set that restates the roster already in `_TELEMETRY_FIELDS` + `_prime_to_fields`. Generate it from the single field source and inject it, so the roster lives in exactly one place.

### Tier 2 — structural consolidation (real tradeoffs; not "fewer processes for its own sake")

**E. Replace Node + bridge.py with one Python ws server (recommended).** Today: `bridge.py` builds a protojson Payload → base64 → **Pub/Sub-push envelope** → HTTP POST → `index.js` base64-decodes → `transformMessage` rebuilds → ws broadcast. That's a double transform + an HTTP hop + an entire **Node runtime**, all to emulate a Google Pub/Sub push to a server that was written for Pub/Sub. Self-hosted, a small Python `websockets` server can tail once and broadcast `data:update` directly. Wins: drops the Node runtime (smaller image, one less language), deletes `bridge.py` + the HTTP hop + the double base64/transform, and pulls the last field-mapping (`transformMessage`) into the shared Python module from **B**. This is the strongest consolidation that **does not touch the wizard process**.

**F. (Optional, with care) Single telemetry-consumer process.** server.py, shim.py, (and the ws) all tail the same stream and each keep their own per-VIN "latest" state. In principle one consumer could tail once into shared state and serve dashboard + shim REST + ws. **Constraint that bounds this:** the wizard/web process must always start first and never fatal (§2). Do **not** fold the streaming/shim/ws failure surface into the wizard process. If pursued, keep the wizard process separate and merge only shim+ws(+bridge) into one *consumer* process — i.e. go (wizard) + (consumer) + binary, not a single mega-process. Higher effort; treat as a later step, not the headline.

### Tier 3 — operational hygiene

**G. Records file grows unbounded within a boot.** `tee -a` appends forever; the file is only truncated at boot (`run.sh:53`) and nothing rotates it (tailers merely *tolerate* shrink as rotation). ~1 MB/h while driving → hundreds of MB over a long uptime in `/tmp`. Fix: cap/rotate it, or — once there is a single consumer (E) — read the binary's stdout via a pipe and **drop the file entirely** (removes `tee` + file + growth).

**H. server.py is a 2,345-line monolith** mixing HTTP server, wizard, NPM client, Tesla OAuth, cert handling, the records tailer, field mapping, and ~40 KB of dashboard HTML/CSS/JS **inside Python strings** (which is why JS has to be extracted to be lint/`node --check`ed). Move the dashboard assets to static files served by the server; split the wizard/NPM/OAuth concerns into modules.

**I. Supervision.** `run.sh` hand-rolls several `(while true; proc; sleep) &` loops + a config-hash poll + a binary-relaunch check + TERM-trap orphan kills. The hassio base ships s6-overlay; defining s6 services (with the config-watch as one service) would be more robust and remove the bespoke supervision code. Medium value; optional.

## 4. Suggested sequencing

1. **Phase 1 (mechanical, safe):** A → B → C → D. Biggest duplication reduction the request targets, with no behavior change, across the existing processes.
2. **Phase 2:** E (Node + bridge.py → one Python ws server). Removes a whole runtime and the double transform.
3. **Phase 3:** G (records file), H (dashboard assets).
4. **Later/optional:** F (consumer consolidation), I (s6).

## 5. One-line takeaway

The duplication the request is about is real and fixable **without** collapsing processes: shared `records.py` + `fields.py` + a single NPM client (Tier 1) remove most of it. The one consolidation worth doing structurally is **Node+bridge → a Python ws server** (Tier 2-E). Leave the wizard process standalone — its "always up, never fatal" property is load-bearing.
