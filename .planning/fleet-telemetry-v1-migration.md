# Fleet Telemetry → v1.0.0 — Migration Path

Target (see `fleet-telemetry-architecture-review-2026-06-17.md` §"target architecture"):
**Tesla's two Go binaries + one async Python `app` (single field model, single ingest, event bus, pluggable sinks), supervised by s6.** Replaces today's bash supervisor + 3 Python processes + Node.

## Guardrails (true after every phase)

- The add-on stays fully working after each phase; each phase is its own commit and is deployed+verified before the next.
- Never regress: **mTLS ingest**, **wizard reachability** (always up, never fatal), the **NPM cert + signed send-config** flow, the **TeslaMate REST shim** (polling) and **streaming ws** (just enabled), **MQTT**.
- **Version stays `0.10.x` through Phases 1–4**; cut to **`1.0.0`** only at Phase 5. `main` keeps shipping 0.10.x; v1 work lives on the `fleet-telemetry-v1` branch/worktree until cutover.
- Each phase verified against **golden fixtures** captured from the live system in Phase 0.

## Phase 0 — Characterization & scaffolding (no behavior change)
- Worktree + `fleet-telemetry-v1` branch.
- Capture golden fixtures from the running add-on: a real `records.jsonl` slice, shim `GET /api/1/vehicles/{id}/vehicle_data`, dashboard `GET /api/state`, a `data:update` ws sequence, and the generated `config.json`.
- Add a `pytest` harness; later phases assert byte-/shape-compatibility against these fixtures.

## Phase 1 — Shared modules (pure dedup, still multi-process)
- `fields.py` — single field model: roster + per-field interval, `_META`, enum-strip, gear map, location parse, and the **bidirectional** telemetry↔vehicle_data table (replaces the inverse `_prime_to_fields` / shim `_assemble`).
- `records.py` — one `tail()` generator (replaces the 3 copies).
- Refactor `server.py` / `shim.py` / `bridge.py` to import both; assert identical output vs fixtures.
- One NPM client: move cert fetch into the existing Python NPM client; **delete `scripts/fetch-npm-cert.sh`** + `run.sh:fetch_cert`.
- Derive the dashboard `grouped` set from `fields.py`.
- Deploy, verify.

## Phase 2 — Python ws sink replaces Node + bridge
- New Python streaming server (asyncio `websockets`) that tails once and emits `data:update`, reusing `fields.py`.
- Remove `teslamate-ws/`, `bridge.py`, `nodejs` from the Dockerfile, and the `start_bridge`/TMWS bash.
- Deploy; verify TeslaMate reconnects and streams on the next drive.

## Phase 3 — The unified `app` (asyncio core)  [sub-steps, each deployable]
- `app/`: ingest reader → `fields` normalize → per-VIN **state store** + asyncio **event bus**.
- 3a: state + bus + **shim REST** sink on the bus.
- 3b: **streaming ws** + **MQTT** sinks on the bus.
- 3c: **dashboard** served by `app`, browser switched to **SSE/WS push** (no 2s poll); assets still inline for now.
- 3d: **control plane** (wizard API, Tesla/OAuth/NPM clients, send-config→proxy) folded in with **task-level error isolation** — a sink crash can never take down the wizard (removes the original reason for process separation).
- Interim: still launched from `run.sh`.

## Phase 4 — Transport & supervision
- Replace `binary stdout │ tee │ /tmp/ft-records.jsonl` with the binary's **native dispatcher** (MQTT to Mosquitto, or a unix-socket/stdout pipe consumed once); **drop the growing file**.
- Convert to **s6-overlay services** `{fleet-telemetry, app, tesla-http-proxy}`; delete the bash supervisor.
- Move dashboard **HTML/CSS/JS to static asset files**.

## Phase 5 — v1.0.0 cutover
- **Config migration:** load old `wizard-config.json`, migrate to the typed (pydantic) config once.
- **Arch:** stay amd64 unless the fleet-telemetry binary is built from source for arm64 (decide; note in DOCS).
- Full E2E vs Phase 0 fixtures + a live drive; update DOCS/README; bump to **`1.0.0`**; deploy; tag `fleet-telemetry-v1.0.0`.

## Rollback / risk
- Every phase = one commit on `fleet-telemetry-v1`; deployable at each. `main` unaffected until cutover.
- Risk hotspots to test hardest: TeslaMate streaming (the `Gear`-gating quirk), cert/mTLS, send-config signing, wizard never-fatal.
