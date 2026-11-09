const task = await taskSpace("groundhog");
const ws = new WebSocket(`ws://127.0.0.1:${GROUNDHOG.port}/ego/${GROUNDHOG.token}`);
const byTarget = new Map();
const bySession = new Map();
const out = (o) => ws.send(JSON.stringify(o));
const TAKEOVER = "Control of this task space is with the user";
let takenOver = false;

function stopOnTakeover(e) {
  if (takenOver || !String(e?.message ?? e).includes(TAKEOVER)) return;
  takenOver = true;
  bySession.clear();
  ws.close(4000, TAKEOVER);
}

async function poll(sid) {
  const entry = bySession.get(sid);
  let idle = 2;
  while (bySession.get(sid) === entry) {
    let evs;
    try { evs = await entry.page.events(); } catch (e) { stopOnTakeover(e); break; }
    for (const e of evs) {
      out({ cid: entry.cid, msg: { method: e.method, params: e.params, sessionId: sid } });
      settleNavigation(entry, e);
    }
    idle = evs.length ? 2 : Math.min(idle * 2, 50);
    if (!evs.length) await new Promise((r) => setTimeout(r, idle));
  }
}

function settleNavigation(entry, e) {
  const nav = entry.nav;
  if (!nav) return;
  const p = e.params || {};
  if (e.method === "Network.requestWillBeSent" && p.type === "Document" && p.frameId === entry.tid) nav.loaderId = p.requestId;
  else if (e.method === "Page.frameNavigated" && !p.frame.parentId) nav.done({ frameId: p.frame.id, loaderId: p.frame.loaderId });
  else if (e.method === "Network.loadingFailed" && p.requestId === nav.loaderId) nav.done({ frameId: entry.tid, loaderId: nav.loaderId, errorText: p.errorText });
}

async function navigate(entry, params) {
  let nav;
  const reply = new Promise((resolve, reject) => {
    const timer = setTimeout(() => nav.done({ frameId: entry.tid, errorText: "net::ERR_TIMED_OUT" }), 85000);
    const settle = (fn) => (v) => { if (entry.nav === nav) { entry.nav = null; clearTimeout(timer); fn(v); } };
    nav = { done: settle(resolve), fail: settle(reject), loaderId: null };
    entry.nav = nav;
  });
  try {
    nav.done(await entry.page.cdp("Page.navigate", params, { timeout: 250 }));
  } catch (e) {
    if (e?.code !== "EGO_CDP_REQUEST_TIMEOUT") nav.fail(e);
  }
  return reply;
}

async function close(tid) {
  const entry = byTarget.get(tid);
  if (!entry) return;
  byTarget.delete(tid);
  for (const [sid, s] of bySession) if (s === entry) bySession.delete(sid);
  await entry.page.close().catch(() => {});
}

async function handle(cid, method, params, sessionId) {
  if (sessionId) {
    const entry = bySession.get(sessionId);
    if (!entry) throw new Error(`session ${sessionId} is not reachable through ego`);
    if (method === "Page.navigate") return navigate(entry, params);
    return entry.page.cdp(method, params, { timeout: 85000 });
  }
  switch (method) {
    case "Target.createTarget": {
      const page = await task.newPage();
      byTarget.set(page.targetId, { page, cid, tid: page.targetId, nav: null });
      return { targetId: page.targetId };
    }
    case "Target.attachToTarget": {
      const entry = byTarget.get(params.targetId);
      if (!entry) throw new Error(`target ${params.targetId} was not created through this bridge`);
      const sid = `ego-${entry.page.label}`;
      bySession.set(sid, entry);
      poll(sid);
      return { sessionId: sid };
    }
    case "Target.closeTarget":
      await close(params.targetId);
      return { success: true };
    default:
      throw new Error(`${method} is not available through ego`);
  }
}

ws.onmessage = async (ev) => {
  const m = JSON.parse(ev.data);
  if (m.gone) {
    for (const [tid, e] of [...byTarget]) if (e.cid === m.cid) await close(tid);
    return;
  }
  const { id, method, params = {}, sessionId } = m.msg;
  const tag = sessionId ? { sessionId } : {};
  try {
    const result = await handle(m.cid, method, params, sessionId);
    out({ cid: m.cid, msg: { id, result: result ?? {}, ...tag } });
  } catch (e) {
    stopOnTakeover(e);
    out({ cid: m.cid, msg: { id, error: { code: -32000, message: String(e?.message ?? e) }, ...tag } });
  }
};

await new Promise((resolve) => { ws.onclose = resolve; });
if (!takenOver) {
  for (const tid of [...byTarget.keys()]) await close(tid);
  await task.finish({ keep: [] }).catch(() => {});
}
