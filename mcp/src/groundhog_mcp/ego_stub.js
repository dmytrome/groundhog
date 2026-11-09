(function stub() {
  const STUB = "(" + stub + ")();";
  const g = globalThis;
  const no = function () { throw new DOMException("blocked", "SecurityError"); };
  const lock = (o, k, v) => { try { Object.defineProperty(o, k, { value: v, writable: false, configurable: false }); } catch (e) {} };
  for (const k of ["WebSocket", "WebSocketStream", "WebTransport", "RTCPeerConnection", "webkitRTCPeerConnection"]) if (k in g) lock(g, k, no);
  const wrap = (Orig) => {
    if (!Orig) return;
    const W = function (url, opts) {
      const abs = new URL(url, g.location.href).href;
      const module = opts && opts.type === "module";
      const body = STUB + (module ? "await import(" + JSON.stringify(abs) + ");" : "importScripts(" + JSON.stringify(abs) + ");");
      const blob = URL.createObjectURL(new Blob([body], { type: "text/javascript" }));
      return new Orig(blob, opts);
    };
    W.prototype = Orig.prototype;
    return W;
  };
  if (g.Worker) lock(g, "Worker", wrap(g.Worker));
  if (g.SharedWorker) lock(g, "SharedWorker", wrap(g.SharedWorker));
  if (g.navigator && g.navigator.serviceWorker) {
    try { lock(Object.getPrototypeOf(g.navigator.serviceWorker), "register", () => Promise.reject(new DOMException("blocked", "SecurityError"))); } catch (e) {}
  }
})();
