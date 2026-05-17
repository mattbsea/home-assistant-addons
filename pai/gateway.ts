// PAI add-on gateway.
//
// Serves the single Home Assistant ingress panel and presents the Pulse
// dashboard and the Claude Code terminal as two tabs, reverse-proxying each
// to its internal service (HTTP and WebSocket).
//
//   /            -> the tab shell (this file)
//   /pulse/...   -> Pulse dashboard      (127.0.0.1:PULSE_PORT, prefix stripped)
//   /terminal/...-> ttyd Claude terminal (127.0.0.1:TTYD_PORT,  prefix kept)

const GATEWAY_PORT = Number(process.env.PAI_GATEWAY_PORT || 31337);
const PULSE_PORT = Number(process.env.PAI_PULSE_PORT || 31338);
const TTYD_PORT = Number(process.env.PAI_TTYD_PORT || 7683);
const TERMINAL_ENABLED = process.env.PAI_TERMINAL_ENABLED !== "false";

const HOP_BY_HOP = ["content-encoding", "content-length", "transfer-encoding"];

function shell(): string {
  const tabs = TERMINAL_ENABLED
    ? `<button class="tab active" data-pane="pulse">Pulse</button>
       <button class="tab" data-pane="terminal">Claude Code</button>`
    : `<button class="tab active" data-pane="pulse">Pulse</button>`;
  const terminalFrame = TERMINAL_ENABLED
    ? `<iframe id="terminal" data-src="terminal/" allow="clipboard-read; clipboard-write"></iframe>`
    : "";
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Personal AI Infrastructure</title>
<style>
  html,body{margin:0;height:100%;background:#111418;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
  #bar{display:flex;height:42px;background:#1c2127;border-bottom:1px solid #2c333d}
  .tab{flex:0 0 auto;padding:0 22px;height:42px;border:0;background:transparent;
       color:#9aa4b2;font-size:14px;cursor:pointer;border-bottom:2px solid transparent}
  .tab:hover{color:#e7ebf0}
  .tab.active{color:#fff;border-bottom-color:#3b82f6}
  #panes{position:absolute;top:42px;left:0;right:0;bottom:0}
  iframe{position:absolute;inset:0;width:100%;height:100%;border:0;display:none;background:#111418}
  iframe.show{display:block}
</style>
</head>
<body>
<div id="bar">${tabs}</div>
<div id="panes">
  <iframe id="pulse" class="show" data-src="pulse/"></iframe>
  ${terminalFrame}
</div>
<script>
  // Lazy-load each pane on first activation so the terminal only connects
  // once the user opens it.
  function activate(name){
    document.querySelectorAll(".tab").forEach(function(t){
      t.classList.toggle("active", t.dataset.pane===name);
    });
    document.querySelectorAll("iframe").forEach(function(f){
      var on = f.id===name;
      if(on && !f.src) f.src = f.dataset.src;
      f.classList.toggle("show", on);
    });
  }
  document.querySelectorAll(".tab").forEach(function(t){
    t.addEventListener("click", function(){ activate(t.dataset.pane); });
  });
  document.getElementById("pulse").src = document.getElementById("pulse").dataset.src;
</script>
</body>
</html>`;
}

const SHELL = shell();

function routeFor(path: string): { port: number; path: string } | null {
  if (path === "/terminal" || path.startsWith("/terminal/")) {
    return TERMINAL_ENABLED ? { port: TTYD_PORT, path } : null;
  }
  if (path === "/pulse" || path.startsWith("/pulse/")) {
    return { port: PULSE_PORT, path: path.slice("/pulse".length) || "/" };
  }
  return null;
}

async function proxyHttp(req: Request, route: { port: number; path: string }): Promise<Response> {
  const url = new URL(req.url);
  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.set("accept-encoding", "identity");

  const init: RequestInit = { method: req.method, headers, redirect: "manual" };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = req.body;
    // @ts-ignore - required by Bun/undici for streaming request bodies
    init.duplex = "half";
  }

  let resp: Response;
  try {
    resp = await fetch(`http://127.0.0.1:${route.port}${route.path}${url.search}`, init);
  } catch {
    return new Response("PAI service is still starting — refresh in a moment.", { status: 502 });
  }

  const h = new Headers(resp.headers);
  for (const name of HOP_BY_HOP) h.delete(name);
  return new Response(resp.body, { status: resp.status, headers: h });
}

const server = Bun.serve({
  port: GATEWAY_PORT,
  hostname: "0.0.0.0",
  async fetch(req, server) {
    const path = new URL(req.url).pathname;

    if ((req.headers.get("upgrade") || "").toLowerCase() === "websocket") {
      const route = routeFor(path);
      if (!route) return new Response("not found", { status: 404 });
      const proto = req.headers.get("sec-websocket-protocol") || "";
      const ok = server.upgrade(req, {
        data: { port: route.port, path: route.path, proto },
        headers: proto ? { "Sec-WebSocket-Protocol": proto.split(",")[0].trim() } : undefined,
      });
      return ok ? undefined : new Response("websocket upgrade failed", { status: 400 });
    }

    if (path === "/" || path === "/index.html") {
      return new Response(SHELL, { headers: { "content-type": "text/html; charset=utf-8" } });
    }

    const route = routeFor(path);
    if (!route) return new Response("Not found", { status: 404 });
    return proxyHttp(req, route);
  },
  websocket: {
    idleTimeout: 255,
    open(ws) {
      const { port, path, proto } = ws.data as { port: number; path: string; proto: string };
      const protocols = proto ? proto.split(",").map((s) => s.trim()).filter(Boolean) : [];
      const upstream = new WebSocket(`ws://127.0.0.1:${port}${path}`, protocols);
      upstream.binaryType = "arraybuffer";
      const queue: any[] = [];
      (ws.data as any).upstream = upstream;
      (ws.data as any).queue = queue;
      upstream.onopen = () => {
        for (const m of queue) upstream.send(m);
        queue.length = 0;
      };
      upstream.onmessage = (e) => { try { ws.send(e.data); } catch {} };
      upstream.onclose = () => { try { ws.close(); } catch {} };
      upstream.onerror = () => { try { ws.close(); } catch {} };
    },
    message(ws, msg) {
      const upstream = (ws.data as any).upstream as WebSocket | undefined;
      if (upstream && upstream.readyState === WebSocket.OPEN) upstream.send(msg);
      else (ws.data as any).queue.push(msg);
    },
    close(ws) {
      try { ((ws.data as any).upstream as WebSocket | undefined)?.close(); } catch {}
    },
  },
});

console.log(
  `[gateway] listening on ${server.port} ` +
  `(pulse=${PULSE_PORT}, ttyd=${TTYD_PORT}, terminal=${TERMINAL_ENABLED})`,
);
