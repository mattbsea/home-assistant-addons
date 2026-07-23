#!/usr/bin/env node
// Thin reverse proxy sitting in front of OmniRoute on the Ingress-facing port.
//
// OmniRoute (Next.js) has no Home Assistant Ingress awareness, and its
// OMNIROUTE_BASE_PATH support does not work end-to-end in this build: setting
// it makes OmniRoute match routes/classify auth against the *raw* prefixed
// path instead of the basePath-stripped one, so `/dashboard` 404s as an
// "unknown route" and `/api/auth/login` 401s. So OmniRoute runs unconfigured
// (bare routing, confirmed working) and this proxy leaves inbound requests
// untouched — matching what Supervisor already forwards after stripping the
// `/api/hassio_ingress/<token>` prefix.
//
// What's still broken bare: OmniRoute's redirects (`/` -> `/dashboard`) and
// `/_next/static/*` asset references are root-absolute, so they resolve
// against the real Home Assistant origin instead of back through the
// Ingress-proxied path. This proxy fixes that on the way OUT: it rewrites
// root-absolute `Location` headers using the `X-Ingress-Path` header
// Supervisor sends, and rewrites `/_next/` (and a few known static asset)
// references in HTML responses to carry that same prefix.
//
// Known limitation: OmniRoute's *client-side* JS calls (e.g. `fetch('/api/...')`
// made after the page hydrates) are compiled without any prefix awareness and
// can't be fixed by rewriting server responses — those still hit the
// unprefixed path on the real HA origin. Interactive dashboard features may
// still fail through Ingress; the "Open Web UI" button (direct, no prefix)
// is unaffected and remains the fully-working path.
"use strict";

const http = require("node:http");
const httpProxy = require("http-proxy");

const LISTEN_PORT = Number(process.env.INGRESS_LISTEN_PORT || 20128);
const UPSTREAM_PORT = Number(process.env.INGRESS_UPSTREAM_PORT || 20130);
const UPSTREAM = `http://127.0.0.1:${UPSTREAM_PORT}`;
const LOG_REQUESTS = (process.env.APP_LOG_LEVEL || "info").toLowerCase() === "debug";

// Root-absolute references this proxy knows how to prefix in HTML bodies.
const ASSET_PREFIXES = ["/_next/", "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png", "/manifest.webmanifest"];

const proxy = httpProxy.createProxyServer({
  target: UPSTREAM,
  xfwd: true,
  selfHandleResponse: true,
});

// OmniRoute gzips HTML responses (Next's `compress: true`). Rewriting the
// body requires reading it as text, so ask upstream not to compress at all —
// simpler and more robust than decompressing gzip ourselves, and the
// dashboard HTML is small enough that this costs nothing meaningful.
proxy.on("proxyReq", (proxyReq) => {
  proxyReq.setHeader("accept-encoding", "identity");
});

function prefixed(value, ingressPath) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return value;
  return ingressPath.replace(/\/+$/, "") + value;
}

function rewriteHtml(html, ingressPath) {
  const base = ingressPath.replace(/\/+$/, "");
  let out = html;
  for (const assetPrefix of ASSET_PREFIXES) {
    out = out.split(`"${assetPrefix}`).join(`"${base}${assetPrefix}`);
  }
  return out;
}

proxy.on("proxyRes", (proxyRes, req, res) => {
  const ingressPath = req.headers["x-ingress-path"];
  const location = proxyRes.headers.location;
  if (ingressPath && location) {
    proxyRes.headers.location = prefixed(location, ingressPath);
  }

  const contentType = proxyRes.headers["content-type"] || "";
  if (!ingressPath || !contentType.includes("text/html")) {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res);
    return;
  }

  // HTML + known ingress context: buffer and rewrite asset references.
  // OmniRoute's dashboard HTML is not attacker-controlled in a way that
  // makes buffering here riskier than the rest of this proxy already is.
  delete proxyRes.headers["content-encoding"]; // body below is always utf8 text
  const chunks = [];
  proxyRes.on("data", (chunk) => chunks.push(chunk));
  proxyRes.on("end", () => {
    const body = rewriteHtml(Buffer.concat(chunks).toString("utf8"), ingressPath);
    proxyRes.headers["content-length"] = Buffer.byteLength(body);
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    res.end(body);
  });
});

proxy.on("error", (err, req, res) => {
  console.error("[ingress-proxy] proxy error:", err.message);
  if (res && typeof res.writeHead === "function" && !res.headersSent) {
    res.writeHead(502, { "Content-Type": "text/plain" });
    res.end("Bad gateway");
  }
});

const server = http.createServer((req, res) => {
  if (LOG_REQUESTS) {
    const start = Date.now();
    console.log(`[ingress-proxy] ${req.method} ${req.url}`);
    res.on("finish", () => {
      console.log(`[ingress-proxy] ${req.method} ${req.url} ${res.statusCode} (${Date.now() - start}ms)`);
    });
  }
  proxy.web(req, res);
});

server.on("upgrade", (req, socket, head) => {
  proxy.ws(req, socket, head);
});

server.listen(LISTEN_PORT, () => {
  console.log(`[ingress-proxy] listening on ${LISTEN_PORT}, forwarding to ${UPSTREAM}`);
});
