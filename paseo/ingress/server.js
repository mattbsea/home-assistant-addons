// Home Assistant sidebar (ingress) panel for Paseo.
//
// Paseo's web app uses absolute paths and has no base-path support, so it cannot run under
// HA's /api/hassio_ingress/<token>/ prefix. Instead this panel frames the daemon's public HTTPS
// URL (PASEO_EXTERNAL_URL, e.g. https://paseo.mbarclay.org), where the web UI runs at the root.
"use strict";

const http = require("node:http");

const PORT = 8099;
// Supervisor's ingress proxy is the only client this port should serve.
const INGRESS_PEER = "172.30.32.2";

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function externalUrl() {
  const raw = (process.env.PASEO_EXTERNAL_URL || "").trim();
  try {
    const url = new URL(raw);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function page(url) {
  if (!url) {
    return `<!doctype html><html><head><meta charset="utf-8"><title>Paseo</title></head>
<body style="font-family:sans-serif;padding:2em">
<p>Set the <code>external_url</code> option of the Paseo add-on to the HTTPS address of the
Paseo daemon (for example <code>https://paseo.example.com</code>) and restart the add-on.</p>
</body></html>`;
  }
  const safe = escapeHtml(url);
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paseo</title>
<style>
  html, body { margin: 0; height: 100%; background: #0b0b0c; }
  iframe { border: 0; width: 100%; height: 100%; display: block; }
  a.open { position: fixed; right: 12px; bottom: 12px; font: 12px sans-serif; color: #aaa;
           background: rgba(0,0,0,.55); padding: 4px 8px; border-radius: 4px; text-decoration: none; }
  a.open:hover { color: #fff; }
</style>
</head>
<body>
<iframe src="${safe}" allow="clipboard-read; clipboard-write; microphone; fullscreen" title="Paseo"></iframe>
<a class="open" href="${safe}" target="_blank" rel="noopener">Open in new tab &#8599;</a>
</body>
</html>`;
}

const server = http.createServer((req, res) => {
  const peer = (req.socket.remoteAddress || "").replace(/^::ffff:/, "");
  if (peer !== INGRESS_PEER && peer !== "127.0.0.1") {
    res.writeHead(403, { "Content-Type": "text/plain" });
    res.end("Forbidden\n");
    return;
  }
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
  res.end(page(externalUrl()));
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`[paseo-addon] sidebar panel listening on :${PORT}`);
});
