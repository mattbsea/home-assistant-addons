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
