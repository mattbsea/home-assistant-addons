# Claude Terminal — Paste Image Support — Design

## Purpose

Let a user paste an image (e.g. a macOS screenshot, Cmd-V) directly into the
`claude-terminal` add-on's web terminal so they can hand it to the `claude`
CLI as a file reference, without manually saving it somewhere and typing a
path by hand.

## Why a file, not an inline image

The `claude` CLI process runs inside the add-on's container, not on the
user's Mac, so it has no way to read the Mac's OS clipboard directly (unlike
running `claude` in a local terminal, where the CLI reads the clipboard via
platform APIs, or the Claude mobile/chat app, which uploads the image
straight into the conversation as an API attachment — neither mechanism is
reachable from a remote/browser-mediated pty). The only thing that can cross
that boundary is bytes over the existing WebSocket connection. So: the
browser captures the pasted image, uploads it to the add-on, the add-on
writes it to a file inside the container, and the file's path is typed into
the terminal as text — which `claude` can then read like any other file
argument.

## Scope

- Applies to the desktop paste path (`Cmd-V` triggering the browser's native
  `paste` event) in `claude-terminal/web-terminal/public/index.html`.
- Does not change the existing mobile paste-button flow (text-only,
  unaffected).
- Single shared scratch folder, not per-tab.

## Architecture

```
Browser (index.html)                    Server (server.js)
──────────────────────                  ──────────────────
'paste' event on terminal element
  → clipboardData has an image item?
      no  → do nothing (xterm's existing
             text-paste handling proceeds)
      yes → preventDefault()
             base64-encode the blob
             ws.send({type:'pasteImage',
                       tabId, data, mimeType})
                                          → decode base64
                                            validate mimeType (png/jpeg/webp)
                                            write ~/.claude-terminal/
                                              pasted-images/
                                              paste-<ISO ts>-<4 hex>.<ext>
                                            prune folder to <=200MB
                                              (delete oldest by mtime first)
                                            reply {type:'pasteImageSaved',
                                                    tabId, path}
                                              or {type:'pasteImageError',
                                                   tabId, message}
  on 'pasteImageSaved'
    → type `path` into the terminal
      belonging to the *response's* tabId
      (not necessarily the currently-active
      tab, in case the user switched tabs
      mid-upload)
    → showToast(`Image pasted: ${path}`)
  on 'pasteImageError'
    → showToast(message, isError)
```

## Client-side details (`index.html`)

- New listener: `terminalEl.addEventListener('paste', handlePasteEvent)`
  registered per-tab alongside the existing `onData`/`onResize` wiring in
  the tab-creation function, so each tab's own xterm element is covered.
- `handlePasteEvent(e)`:
  - Scan `e.clipboardData.items` for an item whose `type` starts with
    `image/`.
  - If found: `e.preventDefault()`, `item.getAsFile()`, read it via
    `FileReader.readAsDataURL` (or `arrayBuffer` + manual base64 — either is
    fine; `readAsDataURL` is simplest since the browser does the base64 work
    and we just strip the `data:image/png;base64,` prefix), then send the
    WebSocket message with the resolved `tabId` closed over from the
    creating scope (same pattern as the existing `onData`/`onResize`
    closures).
  - If not found: return without calling `preventDefault()`, so the
    existing text-paste path (xterm's own handling / the browser's default)
    proceeds untouched.
- New WebSocket message handlers for `pasteImageSaved` / `pasteImageError`,
  added to the existing `ws.onmessage` switch. `pasteImageSaved` sends the
  path as an `input` message to the *response's* `tabId` — reusing the
  existing `send({type:'input', tabId, data})` path, not necessarily
  `activeTabId` (the user may have switched tabs while the upload was in
  flight).
- Reuses the existing `showToast` helper (already used by the mobile copy
  button) for both success and error feedback.

## Server-side details (`server.js`)

New `case 'pasteImage':` in the WebSocket message switch, alongside
`input`/`resize`/`create`/etc:

1. Validate `msg.mimeType` is one of `image/png`, `image/jpeg`, `image/webp`
   — reject anything else with `pasteImageError`.
2. `Buffer.from(msg.data, 'base64')` to decode.
3. Ensure `~/.claude-terminal/pasted-images/` exists (`fs.mkdirSync(...,
   {recursive: true})`).
4. Write the file as `paste-<ISO timestamp with colons stripped>-<4 random
   hex chars>.<ext derived from mimeType>` — the random suffix avoids
   collisions from two pastes in the same second.
5. **Prune**: list the folder, sum sizes, and if the total exceeds
   `200 * 1024 * 1024` bytes, delete files oldest-mtime-first until back
   under the cap (skip the file just written).
6. Reply with the saved absolute path on success, or an error message
   (write failure, bad mimeType) on failure — always tagged with the
   originating `tabId` so the client can route it back to the right
   terminal.

No per-image size limit beyond the folder-level 200MB cap (explicit
decision — rely entirely on folder pruning rather than rejecting individual
large pastes).

## Error handling

- Bad/unsupported mimeType → `pasteImageError`, toast, nothing typed into
  the terminal.
- Write failure (disk full, permissions) → `pasteImageError`, toast.
- WebSocket disconnected mid-upload → client-side send silently no-ops
  (matches existing behavior of other message types over a dead socket);
  no special handling needed beyond what already exists.
- Tab closed while an upload is in flight → server responds with a
  `pasteImageSaved` tagged to a `tabId` that no longer exists on the
  client; client should no-op (check `tabs.has(tabId)` before typing) rather
  than throw.

## Testing plan

No existing automated test suite for this add-on (validated by building the
container and driving it manually, per the project's `CLAUDE.md`). Verify
manually:

- Build the add-on, load the terminal, use Playwright to dispatch a real
  `paste` event with image `clipboardData` (same technique used to verify
  the Cmd-C copy fix) and confirm: the file lands in
  `~/.claude-terminal/pasted-images/` with correct bytes, the path is typed
  into the terminal, and a success toast appears.
- Paste plain text and confirm the existing text-paste behavior is
  untouched (i.e. the image-detection code path doesn't interfere).
- Seed the scratch folder with dummy files past the 200MB cap and confirm
  pruning deletes the oldest ones first and stops once back under the cap.
- Paste an unsupported file type (e.g. paste a copied PDF/file from Finder,
  if that surfaces as clipboard data with a non-image, non-text mimeType)
  and confirm it's rejected with an error toast rather than silently
  failing or corrupting the terminal input.
