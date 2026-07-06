DETECT_AND_COLLECT = r"""
(strip) => {
  const MAX_TEXT = 200;
  const MIN_COMMENT_CHARS = 20;
  // 1.15: near-identical colors (1.0) only — far below WCAG's 4.5:1 readability
  // minimum, since this catches "invisible" and must not flag merely low-contrast text.
  const CONTRAST_THRESHOLD = 1.15;
  const ALPHA_THRESHOLD = 0.05;
  // The exact 1px box `.sr-only` (Tailwind) / `.visually-hidden` (Bootstrap) use — also
  // mimicked for hidden prompts, since it reads as an ordinary accessibility class.
  const TINY_BOX_PX = 1;
  // Ancestors walked to find an effective background color; bounded so an adversarial,
  // deeply-nested page can't turn this into an O(elements x depth) style-recalc cost.
  const MAX_BG_ANCESTORS = 16;
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
  const parseColor = (str) => {
    const m = (str || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map((s) => parseFloat(s));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const relLuminance = ({ r, g, b }) => {
    const f = (c) => {
      const v = c / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const effectiveBg = (el) => {
    let n = el;
    for (let i = 0; n && i < MAX_BG_ANCESTORS; n = n.parentElement, i++) {
      const bg = parseColor(getComputedStyle(n).backgroundColor);
      if (bg && bg.a > ALPHA_THRESHOLD) return bg;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  };
  const isHidden = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden')
      return 'display:none/visibility:hidden';
    if (parseFloat(cs.opacity) <= ALPHA_THRESHOLD) return 'opacity<=' + ALPHA_THRESHOLD;
    if (parseFloat(cs.fontSize) < 4) return 'font-size<4px';
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0 && el.getClientRects().length === 0) return 'zero-size';
    // A real (non-zero) but sub-pixel box; safe here since the walker only reaches
    // elements with non-empty text, and no legitimate visible text renders in 1px.
    const w = parseFloat(cs.width);
    const h = parseFloat(cs.height);
    if (w <= TINY_BOX_PX && h <= TINY_BOX_PX) return 'sr-only-1px';
    // Legacy `clip: rect(...)` hiding — the pre-clip-path version of the same idiom.
    if (cs.clip && /rect\(\s*0[a-z%]*[\s,]+0[a-z%]*[\s,]+0[a-z%]*[\s,]+0[a-z%]*\s*\)/.test(cs.clip))
      return 'clip-zero-rect';
    // Off-canvas (e.g. `left:-9999px`), checked against full document extent so
    // below-the-fold content — still within scrollHeight — is never flagged.
    if (r.width > 0 && r.height > 0) {
      const docW = document.documentElement.scrollWidth;
      const docH = document.documentElement.scrollHeight;
      if (r.right <= 0 || r.bottom <= 0 || r.left >= docW || r.top >= docH) return 'off-screen';
    }
    const fg = parseColor(cs.color);
    if (fg) {
      if (fg.a <= ALPHA_THRESHOLD) return 'text-color-transparent';
      const bg = effectiveBg(el);
      const l1 = relLuminance(fg);
      const l2 = relLuminance(bg);
      const contrast = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      if (contrast < CONTRAST_THRESHOLD) return 'color-contrast<' + CONTRAST_THRESHOLD;
    }
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
  // HTML comments are never part of an element's textContent, so they were never
  // reaching the extracted markdown either way — this is a diagnostic-only signal
  // (a page embedding instructions this way is still worth reporting in threats[]).
  const commentWalker = document.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
  while (commentWalker.nextNode()) {
    const c = commentWalker.currentNode;
    const text = (c.textContent || '').trim();
    if (text.length < MIN_COMMENT_CHARS) continue;
    hidden.push({
      text: text.slice(0, MAX_TEXT), reason: 'html-comment', path: pathOf(c.parentElement),
    });
    if (strip) c.remove();
  }
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
