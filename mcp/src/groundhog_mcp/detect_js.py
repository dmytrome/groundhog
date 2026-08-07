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
  const SVG_NS = 'http://www.w3.org/2000/svg';
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
  // The children a reader sees: a host's shadow tree stands in for its own children, and
  // a `<slot>` stands in for the light nodes assigned to it. `assignedNodes` returns the
  // host's own children, so recursion terminates — a slot never re-enters its own host.
  const childrenOf = (node) => {
    const source = node.shadowRoot ? node.shadowRoot : node;
    const out = [];
    for (const child of Array.prototype.slice.call(source.childNodes)) {
      if (child.nodeType === 1 && child.tagName === 'SLOT' && child.assignedNodes) {
        const assigned = child.assignedNodes({ flatten: true });
        // An unfilled slot renders its own fallback content instead.
        const shown = assigned.length ? assigned : Array.prototype.slice.call(child.childNodes);
        for (const a of shown) out.push(a);
        continue;
      }
      out.push(child);
    }
    return out;
  };
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
    let crossed = false;
    for (let n = el; n && n.nodeType === 1 && parts.length < 5; ) {
      let s = n.tagName.toLowerCase();
      if (n.id) s += '#' + n.id;
      if (crossed) { s += '::shadow'; crossed = false; }
      parts.unshift(s);
      // `parentElement` is null at a shadow boundary — the parent of a shadow root's
      // child is the ShadowRoot, not an Element — so the walk stopped there and every
      // finding inside a component reported a path with no hint of which host it came
      // from. Step to the host instead, and mark where the boundary was.
      if (n.parentElement) { n = n.parentElement; continue; }
      const rootNode = n.getRootNode ? n.getRootNode() : null;
      if (rootNode && rootNode.host) { crossed = true; n = rootNode.host; continue; }
      break;
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
  // Whether anything this element renders paints a box. A Range over its contents
  // measures the content rather than the element, which is what matters for a node that
  // generates no box of its own. A filled `<slot>` needs its assigned nodes measured
  // instead: they are light-DOM children of the host, so they are not inside the slot
  // and a Range over it would report nothing for every slot on the page.
  const rendersContent = (el) => {
    const assigned = el.tagName === 'SLOT' && el.assignedNodes
      ? el.assignedNodes({ flatten: true })
      : null;
    const range = document.createRange();
    if (assigned && assigned.length) {
      for (const node of assigned) {
        if (node.nodeType === 1) {
          if (node.getClientRects().length) return true;
          continue;
        }
        range.selectNode(node);
        if (range.getClientRects().length) return true;
      }
      return false;
    }
    range.selectNodeContents(el);
    return range.getClientRects().length > 0;
  };
  const isHidden = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden')
      return 'display:none/visibility:hidden';
    // The element keeps an ordinary display, a real box and a normal font — only its
    // contents are skipped — so every check below misses it while a reader sees nothing
    // and `innerText` omits it. It reached the extractor as ordinary article text.
    // `checkVisibility()` does not answer this: the element itself is still rendered.
    // `auto` is deliberately not treated as hidden; it renders once scrolled into view.
    if (cs.contentVisibility === 'hidden') return 'content-visibility:hidden';
    // `display: contents` generates no box of its own while its children render in the
    // parent's formatting context, so every box-shaped test reads it as hidden — which
    // made a web component's `<slot>` fallback copy a finding on sight, `<slot>` being
    // `display: contents` by default. Only the box tests are skipped: `font-size` and
    // `color` inherit through it and still hide the text, so those must keep running.
    // Skipping them all let `display:contents` + `font-size:1px` walk straight through.
    // `opacity` is skipped with the box tests — with no box there is nothing to composite.
    const noBox = cs.display === 'contents';
    if (!noBox && parseFloat(cs.opacity) <= ALPHA_THRESHOLD)
      return 'opacity<=' + ALPHA_THRESHOLD;
    if (parseFloat(cs.fontSize) < 4) return 'font-size<4px';
    const r = el.getBoundingClientRect();
    if (noBox) {
      // Generating no box of its own is not the same as rendering nothing, so judge it
      // by what its content paints. Skipping the box tests outright let a `display:
      // contents` element inside a `display:none` subtree through: `zero-size` is the
      // only signal that models "no box because of where this sits", and it was the one
      // being skipped. Measured in Chrome 150 — a legitimate wrapper's contents return
      // 1-2 rects, the same wrapper under `display:none` returns none.
      if (!rendersContent(el)) return 'no-rendered-content';
    } else {
      if (r.width === 0 && r.height === 0 && el.getClientRects().length === 0) return 'zero-size';
      // A real (non-zero) but sub-pixel box; safe here since the walker only reaches
      // elements with non-empty text, and no legitimate visible text renders in 1px.
      const w = parseFloat(cs.width);
      const h = parseFloat(cs.height);
      if (w <= TINY_BOX_PX && h <= TINY_BOX_PX) return 'sr-only-1px';
      // Legacy `clip: rect(...)` hiding — the pre-clip-path version of the same idiom.
      const zeroRect = /rect\(\s*0[a-z%]*[\s,]+0[a-z%]*[\s,]+0[a-z%]*[\s,]+0[a-z%]*\s*\)/;
      if (cs.clip && zeroRect.test(cs.clip)) return 'clip-zero-rect';
    }
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
  // Flagged nodes in the light DOM, addressed later by position in the copy.
  const toRemove = [];
  // Flagged nodes the composition must not re-admit, held by identity because the copy
  // has no shadow trees to address into. Also carries light-DOM nodes: one already
  // removed by position is handed back by `assignedNodes`, since slot assignment is by
  // name and indifferent to whether the node renders.
  const shadowHidden = new Set();
  // Open shadow hosts, outermost first, so composition can mirror the flat tree.
  const lightHosts = [];
  // `createTreeWalker` does not cross a shadow boundary and `contains` does not either,
  // so each tree is walked in its own scope with its own "closest hiding ancestor" set.
  // Without this a shadow tree is never scanned at all — and anything the composition
  // below puts into the markup would arrive unexamined, which is the one thing the
  // strip must never do. Closed roots stay unreachable, and so stay out of the output.
  const scan = (scopeRoot, inShadow, hosts) => {
    const walker = document.createTreeWalker(scopeRoot, NodeFilter.SHOW_ELEMENT);
    const flagged = [];
    const nested = [];
    while (walker.nextNode()) {
      const el = walker.currentNode;
      if (el.shadowRoot) { hosts.push(el); nested.push(el); }
      // A filled `<slot>` has no text of its own — its `textContent` is only the unused
      // fallback — so the check below skips it, yet the nodes it projects inherit their
      // style through it. A bare slotted *text* node has no element of its own for the
      // walk to reach either, so hiding it on the slot was examined by nothing at all.
      if (el.tagName === 'SLOT' && el.assignedNodes) {
        const assigned = el.assignedNodes({ flatten: true });
        const slotReason = assigned.length ? isHidden(el) : null;
        if (slotReason) {
          const projected = assigned.map((n) => n.textContent || '').join('').trim();
          if (projected) {
            hidden.push({
              text: projected.slice(0, MAX_TEXT), reason: slotReason, path: pathOf(el),
            });
          }
          for (const node of assigned) shadowHidden.add(node);
        }
      }
      // `textContent` is node-tree text, so a shadow host reads as empty however much
      // its shadow tree renders — and skipping it here meant `isHidden` was never called
      // on the host at all. `<x-note style="display:none"></x-note>` with the payload in
      // its shadow root was therefore never flagged, and composition then copied it into
      // the output: the scan-before-compose rule broken by its own fast path.
      const shadowText = el.shadowRoot ? (el.shadowRoot.textContent || '').trim() : '';
      const text = (el.textContent || '').trim() || shadowText;
      if (!text) continue;
      if (flagged.some((p) => p.contains(el))) continue;
      const reason = isHidden(el);
      if (reason) {
        // `<script>` and `<style>` compute to `display:none` by definition, so the walk
        // flagged every one of them and made its source an excerpt in the report. That
        // text reaches neither `innerText` nor the extracted markdown, so the finding was
        // never a leak — but it spent the threat cap and handed the page one more
        // attacker-chosen string to put in front of the model. Only the reporting is
        // skipped: both are still flagged and still removed, because "flagged markup
        // never reaches the caller" is the contract the composition below relies on.
        //
        // Gated on the element still not rendering, rather than on the tag alone.
        // `script{display:block}` really does put the source on the page as text
        // (measured in Chrome 150), so a page can render it and then hide it
        // white-on-white — ordinary hidden text that happens to live in a <script>, and
        // still a finding. `localName` because an SVG-namespaced <script> keeps its
        // authored lowercase name and a `tagName` comparison misses it; the namespace
        // test because those come back `display:inline` with no box (so they are caught
        // as `zero-size`, not `display:none`) while SVG renders text only from <text>,
        // which is why inline icon sprites were each reporting their <style> source.
        const sourceOnly =
          (el.localName === 'script' || el.localName === 'style') &&
          (el.namespaceURI === SVG_NS || getComputedStyle(el).display === 'none');
        if (!sourceOnly) {
          hidden.push({ text: text.slice(0, MAX_TEXT), reason, path: pathOf(el) });
        }
        flagged.push(el);
        if (inShadow) shadowHidden.add(el); else toRemove.push(el);
      }
    }
    // Nested hosts are recorded against their own tree, not the light DOM: composition
    // reaches them through their parent's shadow content, not by document position.
    for (const host of nested) scan(host.shadowRoot, true, []);
  };
  scan(root, false, lightHosts);
  // A TreeWalker never returns its own root, so `<body>` was the one element in the page
  // no signal was ever applied to. `content-visibility: hidden` on it kept a principal
  // box, so the layout-collapse check below did not fire either, and text with no element
  // of its own — a bare text node child, or one under a `display: contents` wrapper — had
  // nothing else to catch it. Flagging the root removes the whole subtree, which is the
  // right answer: a page that hides its own body renders nothing a reader could see.
  if (root && !toRemove.includes(root)) {
    const rootReason = isHidden(root);
    if (rootReason) {
      const rootText = (root.textContent || '').trim();
      if (rootText) {
        hidden.push({ text: rootText.slice(0, MAX_TEXT), reason: rootReason, path: pathOf(root) });
      }
      // Ahead of everything the walk found: removing it drops those nodes anyway, and a
      // path resolved inside a removed subtree would no longer address anything.
      toRemove.unshift(root);
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
  // Located by position, never by searching the copy for a <body>. `document.body` is
  // the first body child of <html>, but `querySelector('body')` returns the first one
  // in document order — so a <body> the page appends to <head> is never walked (the
  // walk roots at `document.body`) yet would become the whole of the rebuilt text,
  // replacing the article outright.
  const bodyPath = document.body ? indexPathOf(document.body) : null;
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
  // Built whenever there is shadow content to compose, not only when stripping: a shadow
  // tree is absent from `outerHTML` either way, so `include_hidden` used to return a
  // document with that content silently missing — the exact omission this release closes
  // for the default path. Only the removals below are conditional on `strip`.
  if (strip || lightHosts.length) {
    const inert = document.implementation.createHTMLDocument('');
    copy = inert.importNode(document.documentElement, true);
    // Resolve every path before mutating anything: removing renumbers later siblings,
    // and the composition below replaces whole subtrees.
    const doomed = hiddenPaths.concat(commentPaths).map((parts) => atIndexPath(copy, parts));
    const hostPairs = lightHosts.map((h) => [h, atIndexPath(copy, indexPathOf(h))]);
    for (const node of doomed) {
      // A path that does not resolve means this markup still contains a node that was
      // flagged; say so rather than returning it as fully stripped.
      if (!node) { stripIncomplete = true; continue; }
      node.remove();
    }
    // `importNode` does not carry shadow roots, and neither `outerHTML` nor `innerText`
    // crosses one, so a page rendering through web components used to come back with
    // that content simply missing. It is rebuilt here as ordinary markup — the flat tree
    // a reader actually sees — so the extractor handles it like any other page.
    //
    // Built node by node from the live tree rather than imported wholesale, because the
    // filter is the point: a node flagged inside a shadow tree is skipped here, which is
    // how shadow content can be added to the output without adding an unscanned path
    // into it. `<slot>` is replaced by the nodes assigned to it, so light children
    // appear where they are rendered instead of twice or in the wrong place.
    // Both sets, and checked before the node type is looked at: a flagged node reaches
    // this by two routes — inside a shadow tree, or projected back through a `<slot>`
    // after already being removed from the copy by position — and a projected node can
    // be a text node, which a check placed after the element branch would wave through.
    // Nothing is dropped when the caller asked to keep hidden text: `include_hidden`
    // means the composed shadow tree arrives with its hidden nodes intact, exactly as
    // the light DOM does, and `threats` still reports them.
    const dropped = new Set();
    if (strip) {
      for (const node of shadowHidden) dropped.add(node);
      for (const node of toRemove) dropped.add(node);
    }
    const composed = (live) => {
      if (dropped.has(live)) return null;
      if (live.nodeType === 3) return inert.createTextNode(live.data);
      if (live.nodeType !== 1) return null;
      const el = inert.importNode(live, false);
      for (const child of childrenOf(live)) {
        const built = composed(child);
        if (built) el.appendChild(built);
      }
      return el;
    };
    for (const [live, copyHost] of hostPairs) {
      // A host inside a subtree that was just removed is detached; nothing to fill.
      if (!copyHost || !copy.contains(copyHost)) continue;
      while (copyHost.firstChild) copyHost.removeChild(copyHost.firstChild);
      for (const child of childrenOf(live)) {
        const built = composed(child);
        if (built) copyHost.appendChild(built);
      }
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
  //     `innerText` falls back to raw text and hands back everything it hid. The flagged
  //     descendants *are* hidden by the sheet — it simply does not matter, because
  //     `innerText` on an unrendered element returns textContent without consulting
  //     layout. Reading this as "nothing was flagged" would send the next reader to fix
  //     the walk, which is not where the problem is.
  // Either way the live text is abandoned for the copy the markup was stripped from,
  // where the flagged nodes are gone structurally and no cascade can bring them back.
  // Subtracting the node's text from the string instead was wrong: when its text was a
  // substring of earlier visible text, the visible copy was cut and the payload stayed.
  //
  // An open shadow root takes the same route for a different reason: `innerText` does not
  // cross one, so its text is missing rather than wrong, and the copy is the only place
  // the composed flat tree exists. That is not a failed strip, so it sets no threat —
  // pages without web components keep the live text untouched.
  //
  // Which elements end a line has to come from a tag list: the copy has no layout to
  // read it from, and the branch that hides a resisting node is the one case where the
  // live tree could have answered — using it there would mean two ways to do one thing.
  // So a page styling a listed tag `display:inline` gets a break a reader never saw.
  const BREAKS_LINE =
    'address,article,aside,blockquote,br,caption,center,dd,details,dialog,div,dl,dt,' +
    'fieldset,figcaption,figure,footer,form,h1,h2,h3,h4,h5,h6,header,hgroup,hr,legend,' +
    'li,main,menu,nav,ol,p,pre,search,section,summary,table,tbody,td,tfoot,th,thead,tr,ul';
  if ((strip || lightHosts.length) && document.body) {
    let untrusted = document.body.getClientRects().length === 0;
    if (!untrusted) {
      for (const el of toRemove) {
        if (getComputedStyle(el).display !== 'none') { untrusted = true; break; }
      }
    }
    // Disclosed on the shadow route too. The rebuilt text is read from markup rather
    // than layout, so it can carry what layout suppressed for a reason no signal models
    // — and attaching one throwaway open shadow root is a free way for a page to choose
    // that route. Reporting it costs a threat entry on every web-component page; not
    // reporting it made the weaker source silently selectable.
    if (untrusted || lightHosts.length) {
      // Not a failed strip when the caller asked to keep hidden text — there was no
      // strip to fall short of.
      if (strip) stripIncomplete = true;
      // Never rendered, so it is not part of the text a reader would have seen.
      const noise = copy.querySelectorAll('script,style,noscript,template');
      for (const el of Array.prototype.slice.call(noise)) el.remove();
      // `textContent` concatenates with no regard for layout, so on markup served
      // without whitespace between tags it runs sentences together
      // ("Alpha one.Beta two."). The copy is inert and already serialized into `html`,
      // so a newline is appended to every element that would have ended a line — the
      // same reason invisible width-occupying characters are replaced by a space
      // rather than deleted. `<br>` is in the list rather than replaced: the copy is
      // read only through `textContent`, and a text child sits at the same position in
      // tree order that the `<br>` itself occupied.
      const breaks = copy.querySelectorAll(BREAKS_LINE);
      for (const el of Array.prototype.slice.call(breaks)) {
        el.appendChild(copy.ownerDocument.createTextNode('\n'));
      }
      const body = bodyPath ? atIndexPath(copy, bodyPath) : null;
      // Nesting gives one newline per ancestor, and the source indentation `textContent`
      // preserves adds more. Left alone, wrapper-heavy markup returns text that is mostly
      // blank lines, which spends the token budget and splits every sentence into its own
      // chunk downstream, where blank lines are the chunk boundary.
      text = (body || copy).textContent.replace(/[ \t]*\n(?:[ \t]*\n)+/g, '\n\n').trim();
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
