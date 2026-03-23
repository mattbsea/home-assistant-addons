// Minimal WebSocket echo server for proxy testing
// Run: node echo-test.js
// Connect: wscat -c ws://localhost:7682/ws

const http = require('http');
const { WebSocketServer } = require('ws');

const PORT = parseInt(process.env.WEB_TERMINAL_PORT || '7682', 10);

const server = http.createServer((req, res) => {
    if (req.url === '/echotest') {
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end(`<!DOCTYPE html>
<html><head><title>Echo WS Test</title></head><body>
<pre id="log"></pre>
<script>
var log = document.getElementById('log');
function L(m) { log.textContent += m + '\\n'; }

var bp = location.pathname.replace(/\\/echotest$/, '').replace(/\\/$/, '');
var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
var url = proto + '//' + location.host + bp + '/ws';

L('Connecting: ' + url);
var ws = new WebSocket(url);
var t0 = Date.now();
var n = 0;

ws.onopen = function() {
    L('[' + (Date.now()-t0) + 'ms] OPEN');
    ws.send(JSON.stringify({id: ++n, msg: 'hello'}));
    L('[' + (Date.now()-t0) + 'ms] SENT hello');
};

ws.onmessage = function(e) {
    L('[' + (Date.now()-t0) + 'ms] RECV: ' + e.data);
    // Keep sending every 2 seconds
    setTimeout(function() {
        if (ws.readyState === 1) {
            ws.send(JSON.stringify({id: ++n, msg: 'ping'}));
            L('[' + (Date.now()-t0) + 'ms] SENT ping #' + n);
        }
    }, 2000);
};

ws.onclose = function(e) {
    L('[' + (Date.now()-t0) + 'ms] CLOSE code=' + e.code + ' reason=' + e.reason);
};

ws.onerror = function() {
    L('[' + (Date.now()-t0) + 'ms] ERROR');
};
</script></body></html>`);
        return;
    }
    res.writeHead(404);
    res.end('Not Found');
});

const wss = new WebSocketServer({
    noServer: true,
    perMessageDeflate: false,
});

server.on('upgrade', (request, socket, head) => {
    console.log(`Upgrade: ${request.url}`);
    wss.handleUpgrade(request, socket, head, (ws) => {
        console.log('Client connected');
        let msgCount = 0;

        ws.on('message', (raw) => {
            msgCount++;
            const data = raw.toString('utf-8');
            console.log(`Recv #${msgCount}: ${data}`);
            // Echo back
            const reply = JSON.stringify({ echo: JSON.parse(data), seq: msgCount, ts: Date.now() });
            ws.send(reply, (err) => {
                if (err) console.error(`Send error: ${err.message}`);
                else console.log(`Sent #${msgCount}: ${reply.length} bytes`);
            });
        });

        ws.on('close', (code, reason) => {
            console.log(`Close: code=${code} reason="${reason}" after ${msgCount} messages`);
        });

        ws.on('error', (err) => {
            console.error(`Error: ${err.message}`);
        });
    });
});

server.listen(PORT, '0.0.0.0', () => {
    console.log(`Echo WS test server on port ${PORT}`);
});
