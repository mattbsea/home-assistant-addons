const express = require('express');
const http = require('http');
const path = require('path');
const pty = require('node-pty');
const { WebSocketServer } = require('ws');

const PORT = parseInt(process.env.WEB_TERMINAL_PORT || '7681', 10);
const RING_BUFFER_SIZE = 100 * 1024; // 100KB per session
const ALLOWED_COMMANDS = new Set(['claude', '/bin/bash', '/bin/sh', 'bash', 'sh']);

// Parse tab configuration from environment
let tabConfig = [];
try {
    tabConfig = JSON.parse(process.env.CLAUDE_TAB_CONFIG || '[]');
} catch (e) {
    console.error('Failed to parse CLAUDE_TAB_CONFIG:', e.message);
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
    const msgType = message.type || 'unknown';
    const dataLen = data.length;
    for (const ws of clients) {
        if (ws.readyState === 1) {
            console.log(`SEND [${msgType}] ${dataLen} bytes to client (readyState=${ws.readyState})`);
            try {
                ws.send(data, (err) => {
                    if (err) {
                        console.error(`SEND ERROR [${msgType}]: ${err.message}`);
                    }
                });
            } catch (e) {
                console.error(`SEND THROW [${msgType}]: ${e.message}`);
            }
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

// Diagnostic endpoint - browser POSTs its state here via fetch (no WS needed)
app.post('/api/diag', express.json(), (req, res) => {
    console.log(`BROWSER DIAG: ${JSON.stringify(req.body)}`);
    res.json({ ok: true });
});

app.get('/api/diag', (req, res) => {
    res.json({
        clients: clients.size,
        sessions: sessions.size,
        uptime: process.uptime(),
    });
});

// --- HTTP server + WebSocket server (ws library) ---

const server = http.createServer(app);

const wss = new WebSocketServer({
    noServer: true,
    perMessageDeflate: false,  // CRITICAL: HA ingress strips Sec-WebSocket-Extensions
});

server.on('upgrade', (request, socket, head) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    console.log(`WebSocket upgrade: path=${pathname}`);

    wss.handleUpgrade(request, socket, head, (ws) => {
        handleConnection(ws);
    });
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
            console.log(`WebSocket client ready (NOT yet in broadcast set)`);
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

            case 'subscribe': {
                if (!clients.has(ws)) {
                    clients.add(ws);
                    console.log(`Client subscribed to broadcasts (${clients.size} active)`);
                }
                break;
            }

            case 'list': {
                const listResp = JSON.stringify({
                    type: 'sessions',
                    tabs: getSessionList(),
                    config: tabConfig,
                });
                console.log(`SEND [sessions] ${listResp.length} bytes direct`);
                ws.send(listResp, (err) => {
                    if (err) console.error(`SEND ERROR [sessions]: ${err.message}`);
                    else console.log(`SEND [sessions] callback OK`);
                });
                break;
            }
        }
    });

    ws.on('close', (code, reason) => {
        console.log(`WS CLOSE: code=${code} reason="${reason}" readyState=${ws.readyState}`);
        clients.delete(ws);
        console.log(`WebSocket client disconnected: code=${code} (${clients.size} remaining)`);
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

// Keepalive ping every 30s
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
