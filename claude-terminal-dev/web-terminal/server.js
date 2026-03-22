const express = require('express');
const http = require('http');
const crypto = require('crypto');
const path = require('path');
const pty = require('node-pty');

const PORT = parseInt(process.env.WEB_TERMINAL_PORT || '7681', 10);
const RING_BUFFER_SIZE = 100 * 1024; // 100KB per session
const ALLOWED_COMMANDS = new Set(['claude', '/bin/bash', '/bin/sh', 'bash', 'sh']);
const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

// Parse tab configuration from environment
let tabConfig = [];
try {
    tabConfig = JSON.parse(process.env.CLAUDE_TAB_CONFIG || '[]');
} catch (e) {
    console.error('Failed to parse CLAUDE_TAB_CONFIG:', e.message);
}

// --- Raw WebSocket implementation (no ws library) ---
// This matches libwebsockets/ttyd frame format for HA ingress compatibility

function wsAcceptKey(key) {
    return crypto.createHash('sha1').update(key + WS_GUID).digest('base64');
}

// Encode a WebSocket frame (server→client, unmasked)
function wsEncodeFrame(data, opcode) {
    const payload = typeof data === 'string' ? Buffer.from(data, 'utf-8') : data;
    const len = payload.length;
    let header;
    if (len < 126) {
        header = Buffer.alloc(2);
        header[0] = 0x80 | opcode; // FIN + opcode
        header[1] = len;
    } else if (len < 65536) {
        header = Buffer.alloc(4);
        header[0] = 0x80 | opcode;
        header[1] = 126;
        header.writeUInt16BE(len, 2);
    } else {
        header = Buffer.alloc(10);
        header[0] = 0x80 | opcode;
        header[1] = 127;
        // Write as two 32-bit values (JS safe integer range)
        header.writeUInt32BE(Math.floor(len / 0x100000000), 2);
        header.writeUInt32BE(len >>> 0, 6);
    }
    return Buffer.concat([header, payload]);
}

// Decode incoming WebSocket frames (client→server, masked)
function wsDecodeFrame(buffer) {
    if (buffer.length < 2) return null;
    const byte0 = buffer[0];
    const byte1 = buffer[1];
    const fin = (byte0 >> 7) & 1;
    const opcode = byte0 & 0x0f;
    const masked = (byte1 >> 7) & 1;
    let payloadLen = byte1 & 0x7f;
    let offset = 2;

    if (payloadLen === 126) {
        if (buffer.length < 4) return null;
        payloadLen = buffer.readUInt16BE(2);
        offset = 4;
    } else if (payloadLen === 127) {
        if (buffer.length < 10) return null;
        payloadLen = buffer.readUInt32BE(2) * 0x100000000 + buffer.readUInt32BE(6);
        offset = 10;
    }

    const maskLen = masked ? 4 : 0;
    const totalLen = offset + maskLen + payloadLen;
    if (buffer.length < totalLen) return null;

    let payload;
    if (masked) {
        const mask = buffer.slice(offset, offset + 4);
        payload = Buffer.alloc(payloadLen);
        for (let i = 0; i < payloadLen; i++) {
            payload[i] = buffer[offset + 4 + i] ^ mask[i % 4];
        }
    } else {
        payload = buffer.slice(offset, offset + payloadLen);
    }

    return { fin, opcode, payload, totalLen };
}

// WebSocket connection wrapper
class WsConnection {
    constructor(socket) {
        this.socket = socket;
        this.readyState = 1; // OPEN
        this._buffer = Buffer.alloc(0);
        this._onMessage = null;
        this._onClose = null;
        this._onError = null;

        socket.on('data', (chunk) => this._onData(chunk));
        socket.on('close', () => this._handleClose(1006, ''));
        socket.on('error', (err) => {
            if (this._onError) this._onError(err);
        });
    }

    _onData(chunk) {
        this._buffer = Buffer.concat([this._buffer, chunk]);
        while (this._buffer.length > 0) {
            const frame = wsDecodeFrame(this._buffer);
            if (!frame) break;
            this._buffer = this._buffer.slice(frame.totalLen);
            this._handleFrame(frame);
        }
    }

    _handleFrame(frame) {
        switch (frame.opcode) {
            case 0x01: // text
            case 0x02: // binary
                if (this._onMessage) {
                    this._onMessage(frame.payload);
                }
                break;
            case 0x08: // close
                const code = frame.payload.length >= 2 ? frame.payload.readUInt16BE(0) : 1000;
                const reason = frame.payload.length > 2 ? frame.payload.slice(2).toString('utf-8') : '';
                // Send close response
                this._sendClose(code);
                this._handleClose(code, reason);
                break;
            case 0x09: // ping
                // Respond with pong
                this._writeRaw(wsEncodeFrame(frame.payload, 0x0a));
                break;
            case 0x0a: // pong
                break;
        }
    }

    _handleClose(code, reason) {
        if (this.readyState === 3) return; // already closed
        this.readyState = 3;
        if (this._onClose) this._onClose(code, reason);
        try { this.socket.end(); } catch (e) {}
    }

    _sendClose(code) {
        try {
            const buf = Buffer.alloc(2);
            buf.writeUInt16BE(code, 0);
            this._writeRaw(wsEncodeFrame(buf, 0x08));
        } catch (e) {}
    }

    _writeRaw(data) {
        if (this.socket.writable) {
            this.socket.write(data);
        }
    }

    send(data) {
        if (this.readyState !== 1) return;
        const opcode = typeof data === 'string' ? 0x01 : 0x02;
        this._writeRaw(wsEncodeFrame(data, opcode));
    }

    ping(data) {
        if (this.readyState !== 1) return;
        this._writeRaw(wsEncodeFrame(data || '', 0x09));
    }

    close(code) {
        this._sendClose(code || 1000);
        this._handleClose(code || 1000, '');
    }

    on(event, handler) {
        if (event === 'message') this._onMessage = handler;
        else if (event === 'close') this._onClose = handler;
        else if (event === 'error') this._onError = handler;
    }
}

// --- Ring buffer for session output replay ---

class RingBuffer {
    constructor(maxSize = RING_BUFFER_SIZE) {
        this.chunks = [];
        this.totalSize = 0;
        this.maxSize = maxSize;
    }
    append(data) {
        const buf = Buffer.from(data);
        this.chunks.push(buf);
        this.totalSize += buf.length;
        while (this.totalSize > this.maxSize && this.chunks.length > 1) {
            const removed = this.chunks.shift();
            this.totalSize -= removed.length;
        }
    }
    getContents() {
        return Buffer.concat(this.chunks).toString('utf-8');
    }
    clear() {
        this.chunks = [];
        this.totalSize = 0;
    }
}

// --- Session manager ---

const sessions = new Map();
let initialTabsCreated = false;

function generateId() {
    return Math.random().toString(36).substring(2, 10);
}

function buildSessionEnv(env) {
    const sessionEnv = Object.assign({}, process.env, env || {});
    delete sessionEnv.CLAUDE_TAB_CONFIG;
    delete sessionEnv.WEB_TERMINAL_PORT;
    const home = sessionEnv.HOME || '/home/claude';
    const localBin = home + '/.local/bin';
    if (sessionEnv.PATH && !sessionEnv.PATH.includes(localBin)) {
        sessionEnv.PATH = localBin + ':' + sessionEnv.PATH;
    }
    return sessionEnv;
}

function attachPtyHandlers(session, ptyProcess) {
    ptyProcess.onData((data) => {
        session.ringBuffer.append(data);
        broadcastToClients({ type: 'output', tabId: session.tabId, data });
    });

    ptyProcess.onExit(({ exitCode }) => {
        console.log(`PTY exited for tab "${session.label}" (tabId: ${session.tabId}) with code ${exitCode}`);
        broadcastToClients({ type: 'exited', tabId: session.tabId, exitCode, restart: session.restart });

        if (session.restart) {
            console.log(`Restarting tab "${session.label}" in ${session.restartDelay}s...`);
            setTimeout(() => {
                if (sessions.has(session.tabId)) {
                    session.ringBuffer.clear();
                    respawnSession(session);
                }
            }, session.restartDelay * 1000);
        }
    });
}

function createSession(tabId, { command, args, cwd, label, restart, restartDelay, env }) {
    const shell = command || '/bin/bash';
    const shellArgs = args || [];
    const workDir = cwd || process.env.HOME || '/home/claude';

    let ptyProcess;
    try {
        ptyProcess = pty.spawn(shell, shellArgs, {
            name: 'xterm-256color',
            cols: 80,
            rows: 24,
            cwd: workDir,
            env: buildSessionEnv(env),
        });
    } catch (e) {
        console.error(`Failed to spawn PTY for tab "${label}":`, e.message);
        return null;
    }

    const session = {
        tabId,
        pty: ptyProcess,
        ringBuffer: new RingBuffer(),
        label: label || shell,
        cwd: workDir,
        command: shell,
        args: shellArgs,
        restart: restart || false,
        restartDelay: restartDelay || 32,
        env: env || {},
        pid: ptyProcess.pid,
        cols: 80,
        rows: 24,
    };

    attachPtyHandlers(session, ptyProcess);
    sessions.set(tabId, session);
    console.log(`Session created: "${label}" (tabId: ${tabId}, pid: ${ptyProcess.pid}) in ${workDir}`);
    return session;
}

function respawnSession(session) {
    let ptyProcess;
    try {
        ptyProcess = pty.spawn(session.command, session.args, {
            name: 'xterm-256color',
            cols: session.cols || 80,
            rows: session.rows || 24,
            cwd: session.cwd,
            env: buildSessionEnv(session.env),
        });
    } catch (e) {
        console.error(`Failed to respawn PTY for tab "${session.label}":`, e.message);
        return;
    }

    session.pty = ptyProcess;
    session.pid = ptyProcess.pid;
    attachPtyHandlers(session, ptyProcess);

    broadcastToClients({
        type: 'respawned',
        tabId: session.tabId,
        label: session.label,
    });

    console.log(`Session respawned: "${session.label}" (tabId: ${session.tabId}, pid: ${ptyProcess.pid})`);
}

function destroySession(tabId) {
    const session = sessions.get(tabId);
    if (!session) return;

    session.restart = false;
    try {
        session.pty.kill();
    } catch (e) {}
    sessions.delete(tabId);
    console.log(`Session destroyed: "${session.label}" (tabId: ${tabId})`);
}

function getSessionList() {
    return Array.from(sessions.values()).map((s) => ({
        tabId: s.tabId,
        label: s.label,
        cwd: s.cwd,
        command: s.command,
        restart: s.restart,
    }));
}

function createInitialTabs() {
    if (initialTabsCreated) return;
    initialTabsCreated = true;

    for (const tab of tabConfig) {
        const tabId = tab.tabId || generateId();
        createSession(tabId, {
            command: tab.command,
            args: tab.args || [],
            cwd: tab.cwd,
            label: tab.label,
            restart: tab.restart || false,
            restartDelay: tab.restartDelay || 32,
        });
    }
    console.log(`Created ${tabConfig.length} initial tab(s)`);
}

// --- WebSocket client management ---

const clients = new Set();

function broadcastToClients(message) {
    const data = JSON.stringify(message);
    for (const ws of clients) {
        if (ws.readyState === 1) {
            ws.send(data);
        }
    }
}

// --- Express app ---

const app = express();

app.use(express.static(path.join(__dirname, 'public')));

const nodeModulesDir = path.join(__dirname, 'node_modules');
app.use('/xterm', express.static(path.join(nodeModulesDir, '@xterm/xterm')));
app.use('/xterm-addon-fit', express.static(path.join(nodeModulesDir, '@xterm/addon-fit')));
app.use('/xterm-addon-web-links', express.static(path.join(nodeModulesDir, '@xterm/addon-web-links')));

app.get('/api/config', (req, res) => {
    res.json({ tabs: tabConfig });
});

// --- HTTP server with raw WebSocket upgrade ---

const server = http.createServer(app);

server.on('upgrade', (request, socket, head) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    const key = request.headers['sec-websocket-key'];
    console.log(`WebSocket upgrade: path=${pathname}, origin=${request.headers.origin || 'none'}`);

    if (!key) {
        socket.destroy();
        return;
    }

    // Perform WebSocket handshake manually (matching libwebsockets response format)
    const accept = wsAcceptKey(key);
    const headers = [
        'HTTP/1.1 101 Switching Protocols',
        'Upgrade: websocket',
        'Connection: Upgrade',
        'Sec-WebSocket-Accept: ' + accept,
        '', ''
    ].join('\r\n');

    socket.write(headers);

    // Process any head data
    const ws = new WsConnection(socket);
    if (head && head.length > 0) {
        ws._onData(head);
    }

    // Handle this connection
    handleConnection(ws);
});

function handleConnection(ws) {
    // Do NOT add to clients set yet - the HA ingress two-hop proxy
    // (Core -> Supervisor -> addon) needs time to establish relay.
    // Adding to clients immediately would cause broadcastToClients to
    // send PTY output before the proxy is ready. We add to clients
    // only after the first message arrives (proving the proxy is up).
    let ready = false;
    console.log(`WebSocket client connected (pending ready)`);

    createInitialTabs();

    ws.on('message', (raw) => {
        if (!ready) {
            ready = true;
            clients.add(ws);
            console.log(`WebSocket client ready (${clients.size} active)`);
        }
        let msg;
        try {
            msg = JSON.parse(raw.toString('utf-8'));
        } catch (e) {
            return;
        }

        switch (msg.type) {
            case 'create': {
                const cmd = msg.command || '/bin/bash';
                if (!ALLOWED_COMMANDS.has(cmd)) {
                    console.warn(`Rejected disallowed command: ${cmd}`);
                    break;
                }
                const tabId = msg.tabId || generateId();
                const session = createSession(tabId, {
                    command: cmd,
                    args: msg.args || [],
                    cwd: msg.cwd || process.env.HOME,
                    label: msg.label || 'Shell',
                    restart: msg.restart || false,
                    restartDelay: msg.restartDelay || 32,
                });
                if (session) {
                    broadcastToClients({
                        type: 'created',
                        tabId,
                        label: session.label,
                        cwd: session.cwd,
                    });
                }
                break;
            }

            case 'input': {
                const session = sessions.get(msg.tabId);
                if (session) {
                    session.pty.write(msg.data);
                }
                break;
            }

            case 'resize': {
                const session = sessions.get(msg.tabId);
                if (session && msg.cols > 0 && msg.rows > 0) {
                    session.cols = msg.cols;
                    session.rows = msg.rows;
                    try {
                        session.pty.resize(msg.cols, msg.rows);
                    } catch (e) {}
                }
                break;
            }

            case 'close': {
                destroySession(msg.tabId);
                broadcastToClients({ type: 'closed', tabId: msg.tabId });
                break;
            }

            case 'replay': {
                const session = sessions.get(msg.tabId);
                if (session) {
                    ws.send(JSON.stringify({
                        type: 'replay',
                        tabId: msg.tabId,
                        data: session.ringBuffer.getContents(),
                    }));
                }
                break;
            }

            case 'list': {
                ws.send(JSON.stringify({
                    type: 'sessions',
                    tabs: getSessionList(),
                    config: tabConfig,
                }));
                break;
            }
        }
    });

    ws.on('close', (code, reason) => {
        clients.delete(ws);
        console.log(`WebSocket client disconnected: code=${code}, reason=${reason || 'none'} (${clients.size} remaining)`);
    });

    ws.on('error', (err) => {
        console.error(`WebSocket error: ${err.message}`);
    });
}

// --- Start server ---

server.listen(PORT, '0.0.0.0', () => {
    console.log(`Claude Web Terminal running on port ${PORT}`);
    console.log(`Tab config: ${tabConfig.length} tab(s) configured`);
});

// Keepalive ping every 30s (matching ttyd --ping-interval 30)
setInterval(() => {
    for (const ws of clients) {
        if (ws.readyState === 1) {
            ws.ping();
        }
    }
}, 30000);

// Graceful shutdown
function shutdown() {
    console.log('Shutting down...');
    for (const [tabId] of sessions) {
        destroySession(tabId);
    }
    server.close();
    process.exit(0);
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
