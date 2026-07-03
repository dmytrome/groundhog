DETECT_AND_COLLECT = r"""
(strip) => {
  const MAX_TEXT = 200;
  const hidden = [];
  const pathOf = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 5; n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) s += '#' + n.id;
      parts.unshift(s);
    }
    return parts.join('>');
  };
  const isHidden = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden')
      return 'display:none/visibility:hidden';
    if (parseFloat(cs.opacity) <= 0.05) return 'opacity<=0.05';
    if (parseFloat(cs.fontSize) < 4) return 'font-size<4px';
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0 && el.getClientRects().length === 0) return 'zero-size';
    return null;
  };
  const root = document.body || document.documentElement;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  const toRemove = [];
  while (walker.nextNode()) {
    const el = walker.currentNode;
    const text = (el.textContent || '').trim();
    if (!text) continue;
    // only flag the closest hiding ancestor: skip if a parent already flagged
    if (toRemove.some((p) => p.contains(el))) continue;
    const reason = isHidden(el);
    if (reason) {
      hidden.push({ text: text.slice(0, MAX_TEXT), reason, path: pathOf(el) });
      toRemove.push(el);
    }
  }
  if (strip) for (const el of toRemove) el.remove();
  const meta = {};
  for (const m of document.querySelectorAll('meta[name], meta[property]')) {
    const key = m.getAttribute('name') || m.getAttribute('property');
    const val = m.getAttribute('content');
    if (key && val && !(key in meta)) meta[key] = val;
  }
  const canonEl = document.querySelector('link[rel="canonical"]');
  const langAttr = document.documentElement.getAttribute('lang');
  return { hidden, meta, lang: langAttr || null, canonical: canonEl ? canonEl.href : null };
}
"""
