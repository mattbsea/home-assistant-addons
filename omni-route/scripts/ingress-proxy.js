#!/usr/bin/env node
// Thin reverse proxy sitting in front of OmniRoute on the Ingress-facing port.
//
// OmniRoute is a Next.js app. Home Assistant's Supervisor strips the
// `/api/hassio_ingress/<token>` prefix before forwarding requests to the
// add-on, but OmniRoute (via OMNIROUTE_BASE_PATH -> Next.js `basePath`) is
// configured to only serve routes *under* that same prefix — it uses it to
// generate every redirect, `/_next/static/*` asset URL, and link href it
// emits. Without re-adding the prefix before forwarding upstream, every
// request from the sidebar panel would 404 against OmniRoute's router, and
// every asset/redirect OmniRoute *does* emit would 404 against Supervisor's
// proxy on the way back (it doesn't rewrite response bodies or add prefixes
// either). This proxy makes both sides agree: prefix every request with
// INGRESS_ENTRY before forwarding, so OmniRoute (and everything it renders)
// operates entirely in "prefixed" space, matching what the browser — via
// either the Ingress panel or direct/LAN access to this same port — will
// actually request.
//
// If INGRESS_ENTRY isn't set (Supervisor ingress info unavailable at
// startup), this degrades to a transparent passthrough.
"use strict";

const http = require("node:http");
const httpProxy = require("http-proxy");

const LISTEN_PORT = Number(process.env.INGRESS_LISTEN_PORT || 20128);
const UPSTREAM_PORT = Number(process.env.INGRESS_UPSTREAM_PORT || 20130);
const INGRESS_ENTRY = (process.env.INGRESS_ENTRY || "").replace(/\/+$/, "");
const UPSTREAM = `http://127.0.0.1:${UPSTREAM_PORT}`;
const LOG_REQUESTS = (process.env.APP_LOG_LEVEL || "info").toLowerCase() === "debug";

const proxy = httpProxy.createProxyServer({
  target: UPSTREAM,
  ws: true,
  xfwd: true,
});

function withPrefix(url) {
  if (!INGRESS_ENTRY) return url;
  return url.startsWith(INGRESS_ENTRY) ? url : INGRESS_ENTRY + url;
}

proxy.on("error", (err, req, res) => {
  console.error("[ingress-proxy] proxy error:", err.message);
  if (res && typeof res.writeHead === "function" && !res.headersSent) {
    res.writeHead(502, { "Content-Type": "text/plain" });
    res.end("Bad gateway");
  }
});

const server = http.createServer((req, res) => {
  const originalUrl = req.url;
  req.url = withPrefix(req.url);
  if (LOG_REQUESTS) {
    const start = Date.now();
    console.log(`[ingress-proxy] ${req.method} ${originalUrl} -> ${req.url}`);
    res.on("finish", () => {
      console.log(
        `[ingress-proxy] ${req.method} ${req.url} ${res.statusCode} (${Date.now() - start}ms)` +
          (res.getHeader("location") ? ` location=${res.getHeader("location")}` : "")
      );
    });
  }
  proxy.web(req, res);
});

server.on("upgrade", (req, socket, head) => {
  req.url = withPrefix(req.url);
  proxy.ws(req, socket, head);
});

server.listen(LISTEN_PORT, () => {
  console.log(
    `[ingress-proxy] listening on ${LISTEN_PORT}, forwarding to ${UPSTREAM}` +
      (INGRESS_ENTRY ? ` with prefix "${INGRESS_ENTRY}"` : " (no ingress prefix configured)")
  );
});
