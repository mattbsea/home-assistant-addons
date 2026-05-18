// PAI add-on gateway.
//
// Serves the single Home Assistant ingress panel and presents the Pulse
// dashboard and the Claude Code terminal as two tabs, reverse-proxying each
// to its internal service (HTTP and WebSocket).
//
//   /            -> the tab shell (this file)
//   /pulse/...   -> Pulse dashboard      (127.0.0.1:PULSE_PORT, prefix stripped)
//   /terminal/...-> ttyd Claude terminal (127.0.0.1:TTYD_PORT,  prefix kept)
//   /gateway/... -> helper endpoints (sign-in link detection, code paste-back)
//
// The auth helper exists because copying the Claude Code sign-in URL out of a
// terminal — or pasting the resulting code back in — is awkward on mobile.
// The gateway watches the terminal output for the sign-in URL and exposes it,
// and can inject the pasted code straight into the terminal.

const GATEWAY_PORT = Number(process.env.PAI_GATEWAY_PORT || 31337);
const PULSE_PORT = Number(process.env.PAI_PULSE_PORT || 31338);
const TTYD_PORT = Number(process.env.PAI_TTYD_PORT || 7683);
const TERMINAL_ENABLED = process.env.PAI_TERMINAL_ENABLED !== "false";

const HOP_BY_HOP = ["content-encoding", "content-length", "transfer-encoding"];

// --- Auth helper state ------------------------------------------------------
let detectedAuthUrl: string | null = null;
let activeTerminal: WebSocket | null = null;

const ANSI = /\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_][0-?]*[ -/]*[@-~]/g;
const URL_RE = /https?:\/\/[^\s\x00-\x1f"'`\\<>]+/g;

function scanForAuthUrl(buffer: string): void {
  const clean = buffer.replace(ANSI, "");
  const matches = clean.match(URL_RE) || [];
  for (let i = matches.length - 1; i >= 0; i--) {
    if (/oauth|authoriz/i.test(matches[i])) {
      detectedAuthUrl = matches[i];
      return;
    }
  }
}

function shell(): string {
  const tabs = TERMINAL_ENABLED
    ? `<button class="tab active" data-pane="pulse">Pulse</button>
       <button class="tab" data-pane="terminal">Claude Code</button>`
    : `<button class="tab active" data-pane="pulse">Pulse</button>`;
  const terminalFrame = TERMINAL_ENABLED
    ? `<iframe id="terminal" data-src="terminal/" allow="clipboard-read; clipboard-write"></iframe>`
    : "";
  const pasteUi = TERMINAL_ENABLED
    ? `<button id="pastebtn" title="Paste text into the terminal">Paste</button>
<div id="pastemodal"><div class="card">
  <h3>Paste into the terminal</h3>
  <p class="hint">Paste or type text below, then send it to the Claude Code
     terminal. Useful on mobile, where pasting into the terminal directly is
     unreliable.</p>
  <textarea id="pastetext" placeholder="Paste text here" autocapitalize="off"
     autocomplete="off" autocorrect="off" spellcheck="false"></textarea>
  <div class="acts">
    <button class="sec" id="paste-cancel">Cancel</button>
    <button class="sec" id="paste-send">Send</button>
    <button class="pri" id="paste-run">Send &amp; press Enter</button>
  </div>
</div></div>`
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
  #auth{display:none;background:#1e2630;border-bottom:1px solid #2c333d;
        padding:10px 14px;color:#e7ebf0;font-size:13px}
  #auth.show{display:block}
  #auth .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0}
  #auth b{color:#fff}
  #auth a.btn,#auth button{background:#3b82f6;color:#fff;border:0;border-radius:6px;
        padding:8px 14px;font-size:13px;cursor:pointer;text-decoration:none;display:inline-block}
  #auth button.sec{background:#39424f}
  #auth input{flex:1;min-width:140px;background:#0d1014;border:1px solid #39424f;
        border-radius:6px;color:#e7ebf0;padding:8px;font-size:13px}
  #auth .url{width:100%;font-family:monospace;font-size:11px}
  #auth .msg{color:#7dd3a8;margin-left:4px}
  #auth .close{position:absolute;right:10px;background:transparent;color:#9aa4b2;padding:2px 8px}
  .hint{color:#9aa4b2;font-size:12px}
  #panes{position:absolute;left:0;right:0;bottom:0;top:42px}
  iframe{position:absolute;inset:0;width:100%;height:100%;border:0;display:none;background:#111418}
  iframe.show{display:block}
  #pastebtn{position:fixed;right:14px;bottom:14px;z-index:10;display:none;
        background:#3b82f6;color:#fff;border:0;border-radius:22px;padding:11px 20px;
        font-size:14px;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.45)}
  #pastebtn.show{display:block}
  #pastemodal{position:fixed;inset:0;z-index:20;display:none;
        background:rgba(0,0,0,.6);align-items:center;justify-content:center}
  #pastemodal.show{display:flex}
  #pastemodal .card{background:#1c2127;border:1px solid #2c333d;border-radius:10px;
        padding:16px;width:min(92vw,460px)}
  #pastemodal h3{margin:0 0 6px;color:#fff;font-size:15px}
  #pastemodal p{margin:0 0 10px}
  #pastemodal textarea{width:100%;box-sizing:border-box;height:120px;resize:vertical;
        background:#0d1014;border:1px solid #39424f;border-radius:6px;color:#e7ebf0;
        padding:9px;font-family:monospace;font-size:13px}
  #pastemodal .acts{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;margin-top:10px}
  #pastemodal button{border:0;border-radius:6px;padding:9px 14px;font-size:13px;cursor:pointer}
  #pastemodal .pri{background:#3b82f6;color:#fff}
  #pastemodal .sec{background:#39424f;color:#e7ebf0}
</style>
</head>
<body>
<div id="bar">${tabs}</div>
<div id="auth">
  <button class="close" id="a-close">&times;</button>
  <div class="row"><b>Claude Code sign-in link detected.</b></div>
  <div class="row">
    <a class="btn" id="a-open" target="_blank" rel="noopener">Open sign-in page</a>
    <button class="sec" id="a-copy">Copy link</button>
    <span class="msg" id="a-msg"></span>
  </div>
  <div class="row"><input class="url" id="a-url" readonly onclick="this.select()"></div>
  <div class="row hint">After signing in, paste the code from your browser here:</div>
  <div class="row">
    <input id="a-code" placeholder="Paste the code, then Send" autocapitalize="off" autocomplete="off" spellcheck="false">
    <button id="a-send">Send</button>
  </div>
</div>
<div id="panes">
  <iframe id="pulse" class="show" data-src="pulse/"></iframe>
  ${terminalFrame}
</div>
${pasteUi}
<script>
  function activate(name){
    document.querySelectorAll(".tab").forEach(function(t){
      t.classList.toggle("active", t.dataset.pane===name);
    });
    document.querySelectorAll("iframe").forEach(function(f){
      var on = f.id===name;
      if(on && !f.src) f.src = f.dataset.src;
      f.classList.toggle("show", on);
    });
    var pb=document.getElementById("pastebtn");
    if(pb) pb.classList.toggle("show", name==="terminal");
  }
  document.querySelectorAll(".tab").forEach(function(t){
    t.addEventListener("click", function(){ activate(t.dataset.pane); });
  });
  var pulse=document.getElementById("pulse");
  pulse.src=pulse.dataset.src;

  // --- Auth helper ---
  var auth=document.getElementById("auth");
  var aUrl=document.getElementById("a-url"), aOpen=document.getElementById("a-open");
  var aMsg=document.getElementById("a-msg"), shownUrl=null;
  function flash(t){ aMsg.textContent=t; setTimeout(function(){ aMsg.textContent=""; },2500); }
  document.getElementById("a-close").onclick=function(){ auth.classList.remove("show"); };
  document.getElementById("a-copy").onclick=function(){
    var v=aUrl.value;
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(v).then(function(){flash("Copied");},
        function(){ aUrl.focus(); aUrl.select(); flash("Select + copy"); });
    } else { aUrl.focus(); aUrl.select(); flash("Select + copy"); }
  };
  document.getElementById("a-send").onclick=function(){
    var code=document.getElementById("a-code").value.trim();
    if(!code){ flash("Enter the code first"); return; }
    fetch("gateway/terminal-input",{method:"POST",body:code+"\\r"})
      .then(function(r){ return r.ok?r.json():Promise.reject(); })
      .then(function(){ flash("Sent to terminal"); document.getElementById("a-code").value="";
                         activate("terminal"); })
      .catch(function(){ flash("Could not reach the terminal"); });
  };
  function poll(){
    fetch("gateway/auth-url").then(function(r){return r.json();}).then(function(d){
      if(d.url && d.url!==shownUrl){
        shownUrl=d.url; aUrl.value=d.url; aOpen.href=d.url;
        auth.classList.add("show");
      }
    }).catch(function(){});
  }
  setInterval(poll,2500); poll();

  // --- Paste-into-terminal helper ---
  var pasteBtn=document.getElementById("pastebtn");
  if(pasteBtn){
    var pasteModal=document.getElementById("pastemodal");
    var pasteText=document.getElementById("pastetext");
    pasteBtn.onclick=function(){
      pasteText.value=""; pasteModal.classList.add("show"); pasteText.focus();
    };
    document.getElementById("paste-cancel").onclick=function(){
      pasteModal.classList.remove("show");
    };
    function sendPaste(withEnter){
      var t=pasteText.value;
      if(!t){ pasteModal.classList.remove("show"); return; }
      fetch("gateway/terminal-input",{method:"POST",body: withEnter ? t+"\\r" : t})
        .then(function(r){ if(!r.ok) throw 0; pasteModal.classList.remove("show"); })
        .catch(function(){ alert("Could not reach the terminal. Open the Claude Code tab, then try again."); });
    }
    document.getElementById("paste-send").onclick=function(){ sendPaste(false); };
    document.getElementById("paste-run").onclick=function(){ sendPaste(true); };
  }
</script>
</body>
</html>`;
}

const SHELL = shell();

function routeFor(path: string): { port: number; path: string; terminal: boolean } | null {
  if (path === "/terminal" || path.startsWith("/terminal/")) {
    return TERMINAL_ENABLED ? { port: TTYD_PORT, path, terminal: true } : null;
  }
  if (path === "/pulse" || path.startsWith("/pulse/")) {
    return { port: PULSE_PORT, path: path.slice("/pulse".length) || "/", terminal: false };
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

const JSON_HEADERS = { "content-type": "application/json" };

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
        data: { port: route.port, path: route.path, proto, terminal: route.terminal },
        headers: proto ? { "Sec-WebSocket-Protocol": proto.split(",")[0].trim() } : undefined,
      });
      return ok ? undefined : new Response("websocket upgrade failed", { status: 400 });
    }

    // Auth helper endpoints.
    if (path === "/gateway/auth-url") {
      return new Response(JSON.stringify({ url: detectedAuthUrl }), { headers: JSON_HEADERS });
    }
    if (path === "/gateway/terminal-input") {
      if (req.method !== "POST") return new Response("method not allowed", { status: 405 });
      const text = await req.text();
      if (!activeTerminal || activeTerminal.readyState !== WebSocket.OPEN) {
        return new Response(JSON.stringify({ ok: false }), { status: 503, headers: JSON_HEADERS });
      }
      // ttyd input frame: command byte '0' followed by the payload.
      activeTerminal.send("0" + text.slice(0, 8192));
      return new Response(JSON.stringify({ ok: true }), { headers: JSON_HEADERS });
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
      const data = ws.data as any;
      const { port, path, proto, terminal } = data;
      const protocols = proto ? proto.split(",").map((s: string) => s.trim()).filter(Boolean) : [];
      const upstream = new WebSocket(`ws://127.0.0.1:${port}${path}`, protocols);
      upstream.binaryType = "arraybuffer";
      data.upstream = upstream;
      data.queue = [];
      if (terminal) {
        data.obuf = "";
        detectedAuthUrl = null;
        activeTerminal = upstream;
      }
      upstream.onopen = () => {
        for (const m of data.queue) upstream.send(m);
        data.queue = [];
      };
      upstream.onmessage = (e) => {
        if (terminal) {
          // ttyd OUTPUT frames begin with the command byte '0' (0x30).
          const d = e.data;
          if (d instanceof ArrayBuffer && new Uint8Array(d, 0, 1)[0] === 0x30) {
            data.obuf = (data.obuf + new TextDecoder().decode(new Uint8Array(d, 1))).slice(-16384);
            scanForAuthUrl(data.obuf);
          }
        }
        try { ws.send(e.data); } catch {}
      };
      upstream.onclose = () => { try { ws.close(); } catch {} };
      upstream.onerror = () => { try { ws.close(); } catch {} };
    },
    message(ws, msg) {
      const data = ws.data as any;
      const upstream = data.upstream as WebSocket | undefined;
      if (upstream && upstream.readyState === WebSocket.OPEN) upstream.send(msg);
      else data.queue.push(msg);
    },
    close(ws) {
      const data = ws.data as any;
      if (data.terminal && activeTerminal === data.upstream) activeTerminal = null;
      try { (data.upstream as WebSocket | undefined)?.close(); } catch {}
    },
  },
});

console.log(
  `[gateway] listening on ${server.port} ` +
  `(pulse=${PULSE_PORT}, ttyd=${TTYD_PORT}, terminal=${TERMINAL_ENABLED})`,
);
