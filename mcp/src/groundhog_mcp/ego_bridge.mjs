const task = await taskSpace("groundhog");
const ws = new WebSocket(`ws://127.0.0.1:${GROUNDHOG.port}/ego/${GROUNDHOG.token}`);
const byTarget = new Map();
const bySession = new Map();
const out = (o) => ws.send(JSON.stringify(o));

async function poll(sid) {
  const entry = bySession.get(sid);
  while (bySession.get(sid) === entry) {
    let evs;
    try { evs = await entry.page.events(); } catch { break; }
    for (const e of evs) {
      out({ cid: entry.cid, msg: { method: e.method, params: e.params, sessionId: sid } });
      settleNavigation(entry, e);
    }
    if (!evs.length) await new Promise((r) => setTimeout(r, 2));
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
  const reply = new Promise((resolve) => {
    const timer = setTimeout(() => done({ frameId: entry.tid, errorText: "net::ERR_TIMED_OUT" }), 85000);
    const done = (r) => { if (entry.nav === nav) { entry.nav = null; clearTimeout(timer); resolve(r); } };
    const nav = { done, loaderId: null };
    entry.nav = nav;
  });
  const quick = await entry.page.cdp("Page.navigate", params, { timeout: 250 }).then((r) => r, () => null);
  if (quick && entry.nav) entry.nav.done(quick);
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
    out({ cid: m.cid, msg: { id, error: { code: -32000, message: String(e?.message ?? e) }, ...tag } });
  }
};

await new Promise((resolve) => { ws.onclose = resolve; });
for (const tid of [...byTarget.keys()]) await close(tid);
await task.finish({ keep: [] }).catch(() => {});
