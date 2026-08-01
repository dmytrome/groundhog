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
  // Position among *all* child nodes, from documentElement down. `importNode(el, true)`
  // copies the tree node-for-node, so the same walk locates the node in the copy.
  const indexPathOf = (el) => {
    const parts = [];
    for (let n = el; n && n !== document.documentElement; n = n.parentNode) {
      parts.unshift(Array.prototype.indexOf.call(n.parentNode.childNodes, n));
    }
    return parts;
  };
  const atIndexPath = (rootNode, parts) =>
    parts.reduce((n, i) => (n ? n.childNodes[i] : null), rootNode);
  // The same position as a selector, for the stylesheet that hides it.
  const selectorOf = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
      const at = Array.prototype.indexOf.call(n.parentElement.children, n) + 1;
      parts.unshift(':nth-child(' + at + ')');
    }
    return ':root > ' + parts.join(' > ');
  };
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
  // Nothing below is removed from the live document. `remove()` is [CEReactions]: a
  // custom element's disconnectedCallback would run synchronously as it returned,
  // letting the page write content the reader never saw — as a text node, into an
  // element that already existed, by moving a node, or into `document.title`. Where
  // each half gets its content instead is explained at its own step.
  //
  // Positions are recorded only when they will be used; both walks cost O(siblings)
  // per level, and an `include_hidden` fetch strips nothing.
  const hiddenPaths = strip ? toRemove.map(indexPathOf) : [];
  const hiddenSelectors = strip ? toRemove.map(selectorOf) : [];
  // HTML comments are never part of an element's textContent, so they were never
  // reaching the extracted markdown either way — this is a diagnostic-only signal
  // (a page embedding instructions this way is still worth reporting in threats[]).
  const commentWalker = document.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
  const commentPaths = [];
  while (commentWalker.nextNode()) {
    const c = commentWalker.currentNode;
    const text = (c.textContent || '').trim();
    if (text.length < MIN_COMMENT_CHARS) continue;
    hidden.push({
      text: text.slice(0, MAX_TEXT), reason: 'html-comment', path: pathOf(c.parentElement),
    });
    if (strip) commentPaths.push(indexPathOf(c));
  }
  const meta = {};
  for (const m of document.querySelectorAll('meta[name], meta[property]')) {
    const key = m.getAttribute('name') || m.getAttribute('property');
    const val = m.getAttribute('content');
    if (key && val && !(key in meta)) meta[key] = val;
  }
  const canonEl = document.querySelector('link[rel="canonical"]');
  const langAttr = document.documentElement.getAttribute('lang');
  // Markup: imported into an inert document, not cloned. `cloneNode` is [CEReactions]:
  // it re-creates every custom element with the synchronous flag unset, which enqueues
  // an upgrade reaction drained as it returns — running the page's constructor and
  // attributeChangedCallback inside the strip. A document from `createHTMLDocument` has
  // no browsing context, so definition lookup returns null there and nothing is queued.
  //
  // Imported rather than serialized-and-reparsed: reparsing is not structure-preserving,
  // so the index paths below would address the wrong nodes. Measured in Chrome 150 —
  // adjacent text nodes merge into one, `<noscript>` parses as markup with scripting
  // off, and a script-inserted child of `<table>` is foster-parented out; each shifts
  // every later sibling, and the strip then deleted visible text while leaving the
  // hidden payload in place. `importNode` copies the tree node-for-node.
  const title = document.title;
  // Set by either half when it could not fully carry out the strip, so a partial
  // result is never returned as if it were complete.
  let stripIncomplete = false;
  let html;
  let copy = null;
  if (strip) {
    const inert = document.implementation.createHTMLDocument('');
    copy = inert.importNode(document.documentElement, true);
    // Resolve every path before removing any: removing renumbers later siblings.
    const doomed = hiddenPaths.concat(commentPaths).map((parts) => atIndexPath(copy, parts));
    for (const node of doomed) {
      // A path that does not resolve means this markup still contains a node that was
      // flagged; say so rather than returning it as fully stripped.
      if (!node) { stripIncomplete = true; continue; }
      node.remove();
    }
    html = copy.outerHTML;
  } else {
    html = document.documentElement.outerHTML;
  }

  // Rendered text needs live layout, so the flagged nodes are hidden rather than
  // removed. Adopting a constructed stylesheet is not a tree mutation, so — unlike
  // appending <style> — a MutationObserver sees nothing and the page is never handed
  // the position of every node the detector flagged.
  let adopted = null;
  if (strip && hiddenSelectors.length && 'adoptedStyleSheets' in Document.prototype) {
    adopted = new CSSStyleSheet();
    adopted.replaceSync(hiddenSelectors.join(',') + '{display:none !important}');
    const sheets = Array.prototype.slice.call(document.adoptedStyleSheets);
    sheets.push(adopted);
    document.adoptedStyleSheets = sheets;
  }
  let text = document.body ? document.body.innerText : '';
  // `innerText` is only trustworthy while the sheet above is actually deciding what
  // renders. Two ways it stops being:
  //   * a flagged node wins the cascade — an inline `!important` beats an author sheet,
  //     as do a transition origin and `::slotted` from an inner tree; and
  //   * nothing renders at all, because the page hid its own <body> or <html>. Then
  //     `innerText` falls back to raw text and hands back everything it hid, which the
  //     sheet cannot suppress because the node was never flagged in the first place.
  // Either way the live text is abandoned for the copy the markup was stripped from,
  // where the flagged nodes are gone structurally and no cascade can bring them back.
  // Subtracting the node's text from the string instead was wrong: when its text was a
  // substring of earlier visible text, the visible copy was cut and the payload stayed.
  if (strip && document.body) {
    let untrusted = document.body.getClientRects().length === 0;
    if (!untrusted) {
      for (const el of toRemove) {
        if (getComputedStyle(el).display !== 'none') { untrusted = true; break; }
      }
    }
    if (untrusted) {
      stripIncomplete = true;
      // Never rendered, so it is not part of the text a reader would have seen.
      const noise = copy.querySelectorAll('script,style,noscript,template');
      for (const el of Array.prototype.slice.call(noise)) el.remove();
      const body = copy.querySelector('body');
      text = (body || copy).textContent;
    }
  }
  if (adopted) {
    document.adoptedStyleSheets = Array.prototype.filter.call(
      document.adoptedStyleSheets, (s) => s !== adopted);
  }

  return {
    hidden,
    meta,
    lang: langAttr || null,
    canonical: canonEl ? canonEl.href : null,
    html,
    text,
    title,
    stripIncomplete,
  };
}
"""
