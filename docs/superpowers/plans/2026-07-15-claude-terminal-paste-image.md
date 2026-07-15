# Claude Terminal — Paste Image Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user paste an image (Cmd-V, e.g. a macOS screenshot) into the `claude-terminal` add-on's web terminal, have it uploaded and saved to a file inside the container, and have the resulting file path typed into the terminal so the `claude` CLI can read it like any other file argument.

**Architecture:** Browser intercepts the native `paste` event, detects image clipboard data, base64-encodes it, and sends it over the existing per-connection WebSocket as a new `pasteImage` message. The server decodes it, writes it to a shared scratch folder (`~/.claude-terminal/pasted-images/`), prunes that folder to a size cap, and replies with the saved path (or an error). The client then types that path into the originating tab and shows a toast.

**Tech Stack:** Plain Node.js (`fs`, `path`, `crypto`, the existing `ws` server), Node's built-in `node:test` runner for server-side unit/integration tests (no new dependencies), vanilla browser JS in the existing single-file `index.html`, verified end-to-end with Playwright (already used to verify the Cmd-C fix in this add-on).

## Global Constraints

- Scratch folder: `~/.claude-terminal/pasted-images/` (shared across all tabs, not per-tab).
- Folder size cap: 200 MB total; on each paste, delete oldest-by-mtime files first until back under the cap. Never delete the file just written, even if it alone exceeds the cap.
- No per-image size limit beyond the folder cap (explicit decision from the design spec).
- Allowed mime types: `image/png`, `image/jpeg`, `image/webp`. Anything else is rejected with an error reply, not silently dropped.
- Transport: base64-encoded image data over the existing WebSocket connection (message type `pasteImage`), not a new HTTP endpoint.
- Only the desktop `Cmd-V` → native `paste` event path is in scope. The existing mobile paste-button flow (text-only) is unchanged.
- Spec reference: `docs/superpowers/specs/2026-07-15-claude-terminal-paste-image-design.md`.

---

### Task 1: Image save + pruning logic (`paste-image.js`)

**Files:**
- Create: `claude-terminal/web-terminal/paste-image.js`
- Create: `claude-terminal/web-terminal/test/paste-image.test.js`
- Modify: `claude-terminal/web-terminal/package.json`

**Interfaces:**
- Consumes: nothing (pure Node built-ins: `fs`, `path`, `crypto`).
- Produces (used by Task 2):
  - `resolveImagesDir(home: string): string` — joins `home` with `.claude-terminal/pasted-images`.
  - `saveImage(options: {dir: string, mimeType: string, base64Data: string, maxBytes: number}): string` — writes the decoded image to `dir`, prunes `dir` to `maxBytes`, returns the absolute path written. Throws `Error` (message starts with `"Unsupported image type: "`) for a disallowed `mimeType`.
  - `pruneToSizeLimit(dir: string, maxBytes: number, skipPath?: string): void` — deletes oldest-mtime files in `dir` first until total size is `<= maxBytes`; never deletes `skipPath`; no-ops if `dir` doesn't exist.
  - `MIME_TO_EXT: {[mimeType: string]: string}` — `{'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}`.

- [ ] **Step 1: Write the failing tests**

Create `claude-terminal/web-terminal/test/paste-image.test.js`:

```js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

const pasteImage = require('../paste-image');

function makeTempDir() {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'paste-image-test-'));
}

test('saveImage writes a png with the correct extension', () => {
    const dir = makeTempDir();
    const base64 = Buffer.from('fake-png-bytes').toString('base64');
    const savedPath = pasteImage.saveImage({ dir, mimeType: 'image/png', base64Data: base64, maxBytes: 1024 * 1024 });
    assert.equal(path.dirname(savedPath), dir);
    assert.match(path.basename(savedPath), /^paste-.*\.png$/);
    assert.equal(fs.readFileSync(savedPath, 'utf8'), 'fake-png-bytes');
});

test('saveImage writes a jpeg with the correct extension', () => {
    const dir = makeTempDir();
    const base64 = Buffer.from('fake-jpeg-bytes').toString('base64');
    const savedPath = pasteImage.saveImage({ dir, mimeType: 'image/jpeg', base64Data: base64, maxBytes: 1024 * 1024 });
    assert.match(path.basename(savedPath), /^paste-.*\.jpg$/);
});

test('saveImage creates the directory if it does not exist yet', () => {
    const parent = makeTempDir();
    const dir = path.join(parent, 'nested', 'pasted-images');
    const base64 = Buffer.from('x').toString('base64');
    const savedPath = pasteImage.saveImage({ dir, mimeType: 'image/webp', base64Data: base64, maxBytes: 1024 });
    assert.ok(fs.existsSync(savedPath));
});

test('saveImage rejects an unsupported mime type', () => {
    const dir = makeTempDir();
    assert.throws(() => {
        pasteImage.saveImage({ dir, mimeType: 'application/pdf', base64Data: 'AA==', maxBytes: 1024 });
    }, /Unsupported image type/);
});

test('pruneToSizeLimit deletes oldest files first until back under the cap', () => {
    const dir = makeTempDir();
    // Three 100-byte files, oldest -> newest: a, b, c. Cap = 250 bytes, so
    // exactly the oldest ("a") must go to get down to 200.
    const names = ['a', 'b', 'c'];
    const now = Date.now();
    names.forEach((name, i) => {
        const full = path.join(dir, name);
        fs.writeFileSync(full, Buffer.alloc(100, 65));
        const mtime = new Date(now - (names.length - i) * 60000);
        fs.utimesSync(full, mtime, mtime);
    });

    pasteImage.pruneToSizeLimit(dir, 250);

    assert.deepEqual(fs.readdirSync(dir).sort(), ['b', 'c']);
});

test('pruneToSizeLimit never deletes the just-written file even if it alone exceeds the cap', () => {
    const dir = makeTempDir();
    const oldFile = path.join(dir, 'old.png');
    fs.writeFileSync(oldFile, Buffer.alloc(50, 65));
    const oldMtime = new Date(Date.now() - 60000);
    fs.utimesSync(oldFile, oldMtime, oldMtime);

    const skipPath = path.join(dir, 'new.png');
    fs.writeFileSync(skipPath, Buffer.alloc(500, 66)); // alone bigger than maxBytes

    pasteImage.pruneToSizeLimit(dir, 100, skipPath);

    assert.ok(fs.existsSync(skipPath), 'the just-written file must survive pruning');
    assert.ok(!fs.existsSync(oldFile), 'the older file should have been pruned');
});

test('pruneToSizeLimit no-ops when the directory does not exist', () => {
    const dir = path.join(os.tmpdir(), 'paste-image-test-does-not-exist-' + Date.now());
    assert.doesNotThrow(() => pasteImage.pruneToSizeLimit(dir, 1024));
});

test('resolveImagesDir joins home with the scratch folder path', () => {
    assert.equal(
        pasteImage.resolveImagesDir('/home/claude'),
        path.join('/home/claude', '.claude-terminal', 'pasted-images')
    );
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd claude-terminal/web-terminal && node --test test/paste-image.test.js`
Expected: FAIL — `Cannot find module '../paste-image'` (the module doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `claude-terminal/web-terminal/paste-image.js`:

```js
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const MIME_TO_EXT = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/webp': 'webp',
};

function resolveImagesDir(home) {
    return path.join(home, '.claude-terminal', 'pasted-images');
}

function pruneToSizeLimit(dir, maxBytes, skipPath) {
    let entries;
    try {
        entries = fs.readdirSync(dir);
    } catch (e) {
        return; // directory doesn't exist yet -- nothing to prune
    }

    const files = entries.map(function(name) {
        const full = path.join(dir, name);
        const stat = fs.statSync(full);
        return { full: full, size: stat.size, mtimeMs: stat.mtimeMs };
    });

    let total = files.reduce(function(sum, f) { return sum + f.size; }, 0);
    if (total <= maxBytes) return;

    files.sort(function(a, b) { return a.mtimeMs - b.mtimeMs; }); // oldest first

    for (const f of files) {
        if (total <= maxBytes) break;
        if (skipPath && f.full === skipPath) continue;
        fs.unlinkSync(f.full);
        total -= f.size;
    }
}

function saveImage(options) {
    const dir = options.dir;
    const mimeType = options.mimeType;
    const base64Data = options.base64Data;
    const maxBytes = options.maxBytes;

    const ext = MIME_TO_EXT[mimeType];
    if (!ext) {
        throw new Error('Unsupported image type: ' + mimeType);
    }

    fs.mkdirSync(dir, { recursive: true });

    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const suffix = crypto.randomBytes(2).toString('hex');
    const filename = 'paste-' + timestamp + '-' + suffix + '.' + ext;
    const fullPath = path.join(dir, filename);

    fs.writeFileSync(fullPath, Buffer.from(base64Data, 'base64'));

    pruneToSizeLimit(dir, maxBytes, fullPath);

    return fullPath;
}

module.exports = {
    MIME_TO_EXT: MIME_TO_EXT,
    resolveImagesDir: resolveImagesDir,
    pruneToSizeLimit: pruneToSizeLimit,
    saveImage: saveImage,
};
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd claude-terminal/web-terminal && node --test test/paste-image.test.js`
Expected: PASS — 8 tests, 0 failures.

- [ ] **Step 5: Wire up `npm test`**

Modify `claude-terminal/web-terminal/package.json` — add a `scripts` key (there isn't one currently):

```json
{
  "name": "claude-web-terminal",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "test": "node --test test/"
  },
  "dependencies": {
    "express": "^4.21.0",
    "ws": "^8.18.0",
    "node-pty": "^1.0.0",
    "@xterm/xterm": "^5.5.0",
    "@xterm/addon-fit": "^0.10.0",
    "@xterm/addon-web-links": "^0.11.0"
  }
}
```

Run: `cd claude-terminal/web-terminal && npm test`
Expected: PASS — same 8 tests via the new script.

- [ ] **Step 6: Commit**

```bash
git add claude-terminal/web-terminal/paste-image.js claude-terminal/web-terminal/test/paste-image.test.js claude-terminal/web-terminal/package.json
git commit -m "claude-terminal: add paste-image save/prune module with tests"
```

---

### Task 2: Wire `pasteImage` into the WebSocket server (`server.js`)

**Files:**
- Modify: `claude-terminal/web-terminal/server.js:1-11` (requires + constants)
- Modify: `claude-terminal/web-terminal/server.js:367-377` (message switch)
- Create: `claude-terminal/web-terminal/test/server-paste-image.test.js`

**Interfaces:**
- Consumes: `paste-image.js`'s `resolveImagesDir`, `saveImage` (from Task 1).
- Produces (used by Task 3): the server now accepts `{type: 'pasteImage', tabId, mimeType, data}` over the WebSocket and replies with either `{type: 'pasteImageSaved', tabId, path}` or `{type: 'pasteImageError', tabId, message}`, sent directly back to the requesting connection (not broadcast).

- [ ] **Step 1: Write the failing integration test**

Create `claude-terminal/web-terminal/test/server-paste-image.test.js`:

```js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('node:child_process');
const WebSocket = require('ws');

const SERVER_PATH = path.join(__dirname, '..', 'server.js');

function startServer(port, home) {
    return new Promise((resolve, reject) => {
        const child = spawn(process.execPath, [SERVER_PATH], {
            env: Object.assign({}, process.env, {
                WEB_TERMINAL_PORT: String(port),
                CLAUDE_TAB_CONFIG: '[]',
                AUTO_CONTINUE: 'false',
                HOME: home,
            }),
        });
        let out = '';
        const timer = setTimeout(() => reject(new Error('server did not start in time: ' + out)), 10000);
        const onData = (chunk) => {
            out += chunk.toString();
            if (out.includes('running on port')) {
                clearTimeout(timer);
                child.stdout.removeListener('data', onData);
                resolve(child);
            }
        };
        child.stdout.on('data', onData);
        child.on('error', reject);
    });
}

test('pasteImage over the WebSocket saves a file and replies with its path', async () => {
    const port = 17681;
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-terminal-home-'));
    const child = await startServer(port, home);

    try {
        const ws = new WebSocket('ws://localhost:' + port + '/');
        await new Promise((resolve, reject) => {
            ws.on('open', resolve);
            ws.on('error', reject);
        });

        const tabId = 'test-tab-1';
        const base64 = Buffer.from('fake-png-bytes').toString('base64');

        const saved = await new Promise((resolve, reject) => {
            ws.on('message', (raw) => {
                const msg = JSON.parse(raw.toString('utf-8'));
                if (msg.type === 'pasteImageSaved' && msg.tabId === tabId) resolve(msg);
                if (msg.type === 'pasteImageError' && msg.tabId === tabId) reject(new Error(msg.message));
            });
            ws.send(JSON.stringify({ type: 'list' }));
            ws.send(JSON.stringify({ type: 'pasteImage', tabId: tabId, mimeType: 'image/png', data: base64 }));
        });

        assert.ok(saved.path.startsWith(path.join(home, '.claude-terminal', 'pasted-images')));
        assert.equal(fs.readFileSync(saved.path, 'utf8'), 'fake-png-bytes');

        ws.close();
    } finally {
        child.kill();
    }
});

test('pasteImage with an unsupported mime type replies with pasteImageError', async () => {
    const port = 17682;
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'claude-terminal-home-'));
    const child = await startServer(port, home);

    try {
        const ws = new WebSocket('ws://localhost:' + port + '/');
        await new Promise((resolve, reject) => {
            ws.on('open', resolve);
            ws.on('error', reject);
        });

        const tabId = 'test-tab-2';

        const errMsg = await new Promise((resolve, reject) => {
            ws.on('message', (raw) => {
                const msg = JSON.parse(raw.toString('utf-8'));
                if (msg.type === 'pasteImageError' && msg.tabId === tabId) resolve(msg);
                if (msg.type === 'pasteImageSaved' && msg.tabId === tabId) reject(new Error('expected an error, got saved'));
            });
            ws.send(JSON.stringify({ type: 'list' }));
            ws.send(JSON.stringify({ type: 'pasteImage', tabId: tabId, mimeType: 'application/pdf', data: 'AA==' }));
        });

        assert.match(errMsg.message, /Unsupported image type/);

        ws.close();
    } finally {
        child.kill();
    }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd claude-terminal/web-terminal && node --test test/server-paste-image.test.js`
Expected: FAIL — times out waiting for a `pasteImageSaved`/`pasteImageError` message (the server doesn't handle `pasteImage` yet, so nothing comes back).

- [ ] **Step 3: Add the require and constants**

Modify `claude-terminal/web-terminal/server.js` — change lines 1-11 from:

```js
const express = require('express');
const http = require('http');
const path = require('path');
const pty = require('node-pty');
const { WebSocketServer } = require('ws');
const { AutoContinueWatcher } = require('./auto-continue');

const PORT = parseInt(process.env.WEB_TERMINAL_PORT || '7681', 10);
const AUTO_CONTINUE = (process.env.AUTO_CONTINUE || 'true').toLowerCase() !== 'false';
const RING_BUFFER_SIZE = 512 * 1024; // 512KB per session
const ALLOWED_COMMANDS = new Set(['claude', '/bin/bash', '/bin/sh', 'bash', 'sh']);
```

to:

```js
const express = require('express');
const http = require('http');
const path = require('path');
const pty = require('node-pty');
const { WebSocketServer } = require('ws');
const { AutoContinueWatcher } = require('./auto-continue');
const pasteImage = require('./paste-image');

const PORT = parseInt(process.env.WEB_TERMINAL_PORT || '7681', 10);
const AUTO_CONTINUE = (process.env.AUTO_CONTINUE || 'true').toLowerCase() !== 'false';
const RING_BUFFER_SIZE = 512 * 1024; // 512KB per session
const ALLOWED_COMMANDS = new Set(['claude', '/bin/bash', '/bin/sh', 'bash', 'sh']);
const PASTE_IMAGES_DIR = pasteImage.resolveImagesDir(process.env.HOME || '/home/claude');
const PASTE_IMAGES_MAX_BYTES = 200 * 1024 * 1024; // 200MB
```

- [ ] **Step 4: Add the `pasteImage` case to the message switch**

Modify `claude-terminal/web-terminal/server.js` — the `case 'list':` block currently ends with (matching the existing lines just before the switch's closing brace):

```js
            case 'list': {
                ws.send(JSON.stringify({
                    type: 'sessions',
                    tabs: getSessionList(),
                    config: tabConfig,
                    activeTabId: activeTabId,
                    version: process.env.ADDON_VERSION || '',
                }));
                break;
            }
        }
    });
```

Change it to:

```js
            case 'list': {
                ws.send(JSON.stringify({
                    type: 'sessions',
                    tabs: getSessionList(),
                    config: tabConfig,
                    activeTabId: activeTabId,
                    version: process.env.ADDON_VERSION || '',
                }));
                break;
            }

            case 'pasteImage': {
                try {
                    const savedPath = pasteImage.saveImage({
                        dir: PASTE_IMAGES_DIR,
                        mimeType: msg.mimeType,
                        base64Data: msg.data,
                        maxBytes: PASTE_IMAGES_MAX_BYTES,
                    });
                    ws.send(JSON.stringify({ type: 'pasteImageSaved', tabId: msg.tabId, path: savedPath }));
                } catch (e) {
                    ws.send(JSON.stringify({ type: 'pasteImageError', tabId: msg.tabId, message: e.message }));
                }
                break;
            }
        }
    });
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd claude-terminal/web-terminal && node --test test/server-paste-image.test.js`
Expected: PASS — 2 tests, 0 failures.

- [ ] **Step 6: Run the full test suite**

Run: `cd claude-terminal/web-terminal && npm test`
Expected: PASS — all tests from Task 1 and Task 2 (10 total), 0 failures.

- [ ] **Step 7: Commit**

```bash
git add claude-terminal/web-terminal/server.js claude-terminal/web-terminal/test/server-paste-image.test.js
git commit -m "claude-terminal: handle pasteImage WebSocket messages in server.js"
```

---

### Task 3: Client-side paste handling, version bump, and ship

**Files:**
- Modify: `claude-terminal/web-terminal/public/index.html` (paste listener in `addTab`, new `handleTerminalPaste` function, two new `handleMessage` cases)
- Modify: `claude-terminal/config.yaml`
- Modify: `claude-terminal/CHANGELOG.md`

**Interfaces:**
- Consumes: `send(msg)` (existing, `index.html:104`), `showToast(msg)` (existing, `index.html:473`), the `tabs` map (existing, `index.html:51`), the server's `pasteImage`/`pasteImageSaved`/`pasteImageError` protocol from Task 2.
- Produces: nothing consumed by a later task — this is the final, user-facing wiring.

- [ ] **Step 1: Add the paste listener when a tab is created**

Modify `claude-terminal/web-terminal/public/index.html` — in the `addTab` function, change:

```js
        terminal.open(wrapper);

        // Custom link provider that handles URLs spanning multiple terminal rows.
```

to:

```js
        terminal.open(wrapper);

        wrapper.addEventListener('paste', function(e) {
            handleTerminalPaste(e, tabId);
        });

        // Custom link provider that handles URLs spanning multiple terminal rows.
```

- [ ] **Step 2: Add the `handleTerminalPaste` function**

Modify `claude-terminal/web-terminal/public/index.html` — right after the `addTab` function's closing brace (immediately before the `// Switch active tab` comment), add:

```js
    // Detect an image on the system clipboard (e.g. a macOS screenshot) pasted into a
    // terminal tab. The `claude` CLI runs inside this container, not on the user's
    // machine, so it can't read the OS clipboard directly -- upload the image bytes to
    // the add-on instead, which saves them to a file and hands back a path the CLI can
    // read like any other file argument.
    function handleTerminalPaste(e, tabId) {
        var items = (e.clipboardData && e.clipboardData.items) || [];
        var imageItem = null;
        for (var i = 0; i < items.length; i++) {
            if (items[i].type && items[i].type.indexOf('image/') === 0) {
                imageItem = items[i];
                break;
            }
        }
        if (!imageItem) return; // no image on the clipboard: let normal text paste proceed

        e.preventDefault();
        var file = imageItem.getAsFile();
        if (!file) return;

        var reader = new FileReader();
        reader.onload = function() {
            var dataUrl = reader.result || '';
            var comma = dataUrl.indexOf(',');
            var base64 = comma >= 0 ? dataUrl.slice(comma + 1) : '';
            send({ type: 'pasteImage', tabId: tabId, mimeType: imageItem.type, data: base64 });
        };
        reader.readAsDataURL(file);
    }

```

- [ ] **Step 3: Add the `handleMessage` cases**

Modify `claude-terminal/web-terminal/public/index.html` — change:

```js
            case 'closed': {
                removeTab(msg.tabId);
                break;
            }
        }
    }
```

to:

```js
            case 'closed': {
                removeTab(msg.tabId);
                break;
            }

            case 'pasteImageSaved': {
                if (tabs.has(msg.tabId)) {
                    send({ type: 'input', tabId: msg.tabId, data: msg.path });
                }
                showToast('Image pasted: ' + msg.path);
                break;
            }

            case 'pasteImageError': {
                showToast('Image paste failed: ' + (msg.message || 'unknown error'));
                break;
            }
        }
    }
```

- [ ] **Step 4: Verify with Playwright against a running server**

This add-on has no browser-side test runner (it's a single-file inline script with no build step), so verify with a short-lived Playwright script against a real running instance — the same technique already used and proven in this project to verify the Cmd-C copy fix.

Start the server:

```bash
cd claude-terminal/web-terminal
WEB_TERMINAL_PORT=7683 CLAUDE_TAB_CONFIG='[]' AUTO_CONTINUE=false node server.js &
```

Write `/tmp/verify-paste-image.js` (adjust the path to your scratch directory):

```js
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const os = require('os');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:7683/');
  await page.waitForTimeout(500);
  await page.click('#new-tab-btn');
  await page.waitForSelector('.terminal-wrapper.active .xterm-helper-textarea', { timeout: 15000 });
  await page.waitForTimeout(1200);

  // A minimal valid 1x1 PNG, base64-encoded.
  const onePixelPng = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64'
  );

  const result = await page.evaluate(async (b64) => {
    const wrapper = document.querySelector('.terminal-wrapper.active');
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const file = new File([bytes], 'screenshot.png', { type: 'image/png' });
    const dt = new DataTransfer();
    dt.items.add(file);
    const event = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    wrapper.dispatchEvent(event);
    // give the async FileReader + WebSocket round trip time to complete
    await new Promise((r) => setTimeout(r, 1000));
    return {
      toastText: document.getElementById('toast').textContent,
      terminalText: document.querySelector('.terminal-wrapper.active .xterm-rows').innerText,
    };
  }, onePixelPng.toString('base64'));

  console.log('toast:', JSON.stringify(result.toastText));
  console.log('terminal tail:', JSON.stringify(result.terminalText.slice(-200)));

  await browser.close();
})();
```

Run: `node /tmp/verify-paste-image.js`

Expected: `toast` contains `"Image pasted: "` followed by a path ending in `.claude-terminal/pasted-images/paste-....png`, and `terminal tail` shows that same path having been typed into the shell prompt (it'll appear as unexecuted text after the `$ ` prompt, since the script doesn't press Enter).

Then confirm the file actually landed on disk:

```bash
ls -la ~/.claude-terminal/pasted-images/
```

Expected: one `paste-*.png` file present, non-zero size.

Then confirm plain-text paste is unaffected (the spec explicitly calls this out — the image-detection code must not interfere with normal text paste). Append to the same script before `browser.close()`:

```js
  const textResult = await page.evaluate(async () => {
    const wrapper = document.querySelector('.terminal-wrapper.active');
    const dt = new DataTransfer();
    dt.setData('text/plain', 'PLAIN_TEXT_PASTE_CHECK');
    const event = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    wrapper.dispatchEvent(event);
    await new Promise((r) => setTimeout(r, 300));
    return document.querySelector('.terminal-wrapper.active .xterm-rows').innerText;
  });
  console.log('text paste tail:', JSON.stringify(textResult.slice(-100)));
```

Expected: `text paste tail` includes `PLAIN_TEXT_PASTE_CHECK` — confirming the image-detection listener didn't swallow or interfere with a normal text paste.

Clean up:

```bash
kill %1  # stop the server started above
rm /tmp/verify-paste-image.js
rm -rf ~/.claude-terminal/pasted-images/
```

- [ ] **Step 5: Bump version and changelog**

Modify `claude-terminal/config.yaml` line 4 from:

```yaml
version: "2.0.14"
```

to:

```yaml
version: "2.0.15"
```

Modify `claude-terminal/CHANGELOG.md` — change:

```markdown
# Changelog

## 2.0.14
```

to:

```markdown
# Changelog

## 2.0.15

### ✨ Improvements
- **Paste an image (e.g. a macOS screenshot) directly into the terminal.** Since the `claude`
  CLI runs inside this container and can't read your Mac's clipboard directly, pasting an image
  now uploads it to the add-on, saves it to `~/.claude-terminal/pasted-images/`, and types the
  resulting file path into the terminal so you can hand it to Claude like any other file
  argument. The scratch folder is capped at 200MB, pruning the oldest images first. Plain text
  paste is unaffected.

## 2.0.14
```

- [ ] **Step 6: Run the full test suite one more time**

Run: `cd claude-terminal/web-terminal && npm test`
Expected: PASS — all 10 tests, 0 failures (confirms the version/changelog edits didn't touch any tested code path).

- [ ] **Step 7: Commit**

```bash
git add claude-terminal/web-terminal/public/index.html claude-terminal/config.yaml claude-terminal/CHANGELOG.md
git commit -m "claude-terminal 2.0.15: paste image into terminal as a file reference"
```

- [ ] **Step 8: Push and deploy**

```bash
git push origin main
```

Then follow this project's standard add-on update flow (see memory `update-local-addon-in-ha`): force a Supervisor store reload via the SSH add-on container (`ha store reload`), then `ha_manage_addon(slug="a0d7b954_claude_terminal", action="update")` (confirm the exact slug with `ha_get_addon()` first — it may differ from the fleet-telemetry example). **Note:** this add-on hosts the very session doing the deploying if run from inside Claude Terminal — confirm with the user before restarting it, since the session will disconnect.
