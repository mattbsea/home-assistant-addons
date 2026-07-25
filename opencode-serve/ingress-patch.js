(function(){
  var INGRESS = "__INGRESS_ENTRY__";
  if (!INGRESS) return;
  var ORIGIN = location.origin;

  function rewriteUrl(u) {
    if (typeof u !== "string") return u;
    if (u.indexOf(ORIGIN + "/api/") === 0 || u.indexOf(ORIGIN + "/event") === 0)
      return ORIGIN + INGRESS + u.slice(ORIGIN.length);
    if (u.charAt(0) === "/" && (u.indexOf("/api/") === 0 || u.indexOf("/event") === 0))
      return INGRESS + u;
    return u;
  }

  // OpenCode's generated API client issues requests as `fetch(new Request(url, init))`,
  // not `fetch(urlString, init)` — a plain string rewrite misses every call.
  var origFetch = window.fetch;
  window.fetch = function(input, init) {
    if (typeof input === "string") {
      return origFetch.call(this, rewriteUrl(input), init);
    }
    if (input instanceof Request) {
      var rewritten = rewriteUrl(input.url);
      if (rewritten !== input.url) input = new Request(rewritten, input);
    }
    return origFetch.call(this, input, init);
  };

  var OrigEventSource = window.EventSource;
  window.EventSource = function(url, opts) { return new OrigEventSource(rewriteUrl(url), opts); };
  window.EventSource.prototype = OrigEventSource.prototype;
  window.EventSource.CONNECTING = OrigEventSource.CONNECTING;
  window.EventSource.OPEN = OrigEventSource.OPEN;
  window.EventSource.CLOSED = OrigEventSource.CLOSED;
})();
