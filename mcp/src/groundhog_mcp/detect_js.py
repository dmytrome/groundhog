DETECT_AND_COLLECT = r"""
(strip) => {
  const MAX_TEXT = 200;
  const MIN_COMMENT_CHARS = 20;
  const MIN_ATTRIBUTE_CHARS = 125;
  const TEXT_ATTRIBUTES = ['alt', 'aria-label', 'aria-description', 'title'];
  const HIDING_ATTRIBUTES = ['hidden', 'aria-hidden', 'style'];
  const HIDING_NAME = /hid(e|den)/i;
  const ATTRIBUTE_CARRIER_SELECTOR = TEXT_ATTRIBUTES.map((n) => '[' + n + ']').join(',');
  const CONTRAST_THRESHOLD = 1.15;
  const ALPHA_THRESHOLD = 0.05;
  const TINY_BOX_PX = 1;
  const MAX_BG_ANCESTORS = 16;
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const hidden = [];
  const MAX_SPANS = 400;
  const MAX_LOW_SIGNAL_SPANS = 100;
  let spansDropped = 0;
  let lowSignalSpans = 0;
  const report = (span) => {
    const lowSignal = span.reason.indexOf('attribute:') === 0 || span.reason === 'template';
    if (lowSignal && lowSignalSpans >= MAX_LOW_SIGNAL_SPANS) { spansDropped++; return; }
    if (hidden.length >= MAX_SPANS) { spansDropped++; return; }
    if (lowSignal) lowSignalSpans++;
    hidden.push(span);
  };
  const indexPathOf = (el) => {
    const parts = [];
    for (let n = el; n && n !== document.documentElement; n = n.parentNode) {
      parts.unshift(Array.prototype.indexOf.call(n.parentNode.childNodes, n));
    }
    return parts;
  };
  const atIndexPath = (rootNode, parts) =>
    parts.reduce((n, i) => (n ? n.childNodes[i] : null), rootNode);
  const childrenOf = (node) => {
    const source = node.shadowRoot ? node.shadowRoot : node;
    const out = [];
    for (const child of Array.prototype.slice.call(source.childNodes)) {
      if (child.nodeType === 1 && child.tagName === 'SLOT' && child.assignedNodes) {
        const assigned = child.assignedNodes({ flatten: true });
        const shown = assigned.length ? assigned : Array.prototype.slice.call(child.childNodes);
        for (const a of shown) out.push(a);
        continue;
      }
      out.push(child);
    }
    return out;
  };
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
      if (n.parentElement) { n = n.parentElement; continue; }
      const rootNode = n.getRootNode ? n.getRootNode() : null;
      if (rootNode && rootNode.host) { crossed = true; n = rootNode.host; continue; }
      break;
    }
    return parts.join('>');
  };
  const colorCanvas = (() => {
    try {
      const c = document.createElement('canvas');
      c.width = c.height = 1;
      return c.getContext('2d', { willReadFrequently: true });
    } catch (e) {
      return null;
    }
  })();
  const MAX_COLOR_CACHE = 512;
  const colorCache = new Map();
  const viaCanvas = (str) => {
    colorCanvas.fillStyle = '#000000';
    colorCanvas.fillStyle = str;
    const resolved = colorCanvas.fillStyle;
    colorCanvas.fillStyle = '#ffffff';
    colorCanvas.fillStyle = str;
    if (colorCanvas.fillStyle !== resolved) return null;
    colorCanvas.clearRect(0, 0, 1, 1);
    colorCanvas.fillRect(0, 0, 1, 1);
    const d = colorCanvas.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const parseColor = (str) => {
    if (!str) return null;
    if (colorCache.has(str)) return colorCache.get(str);
    let out = null;
    const m = str.match(/^rgba?\(([^)]+)\)$/);
    if (m) {
      const p = m[1].split(',').map((s) => parseFloat(s));
      if (p.length >= 3 && !p.some((v) => Number.isNaN(v)))
        out = { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
    }
    if (!out && colorCanvas) {
      try {
        out = viaCanvas(str);
      } catch (e) {
        out = null;
      }
    }
    if (colorCache.size < MAX_COLOR_CACHE) colorCache.set(str, out);
    return out;
  };
  const blend = (layers, under) => {
    let out = under;
    for (let i = layers.length - 1; i >= 0; i--) {
      const t = layers[i];
      out = {
        r: t.r * t.a + out.r * (1 - t.a),
        g: t.g * t.a + out.g * (1 - t.a),
        b: t.b * t.a + out.b * (1 - t.a),
        a: 1,
      };
    }
    return out;
  };
  const relLuminance = ({ r, g, b }) => {
    const f = (c) => {
      const v = c / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const WHITE = { r: 255, g: 255, b: 255, a: 1 };
  const DARK_CANVAS = { r: 18, g: 18, b: 18, a: 1 };
  const canvasBg = (() => {
    let declared = getComputedStyle(document.documentElement).colorScheme || '';
    if (!declared || declared === 'normal') {
      const meta = document.querySelector('meta[name="color-scheme" i]');
      declared = (meta && meta.getAttribute('content')) || '';
    }
    if (!/(^|\s)dark(\s|$)/.test(declared)) return WHITE;
    if (!/(^|\s)light(\s|$)/.test(declared)) return DARK_CANVAS;
    return matchMedia('(prefers-color-scheme: dark)').matches ? DARK_CANVAS : WHITE;
  })();
  const ownsText = (el) => {
    const assigned = el.tagName === 'SLOT' && el.assignedNodes
      ? el.assignedNodes({ flatten: true })
      : null;
    const nodes = assigned && assigned.length ? assigned : el.childNodes;
    for (const node of nodes) {
      if (node.nodeType === 3 && node.data.trim()) return true;
    }
    if (el.shadowRoot) {
      for (const node of el.shadowRoot.childNodes) {
        if (node.nodeType === 3 && node.data.trim()) return true;
      }
    }
    return false;
  };
  const pageBg = (() => {
    const layers = [];
    for (const n of [document.body, document.documentElement]) {
      if (!n) continue;
      const bg = parseColor(getComputedStyle(n).backgroundColor);
      if (!bg || bg.a === 0) continue;
      if (bg.a >= 1) return blend(layers, bg);
      layers.push(bg);
    }
    return blend(layers, canvasBg);
  })();
  const effectiveBg = (el) => {
    const layers = [];
    let n = el;
    for (let i = 0; n && i < MAX_BG_ANCESTORS; i++) {
      if (n === document.body || n === document.documentElement) break;
      const bg = parseColor(getComputedStyle(n).backgroundColor);
      if (bg && bg.a >= 1) return blend(layers, bg);
      if (bg && bg.a !== 0) layers.push(bg);
      if (n.parentElement) {
        n = n.parentElement;
        continue;
      }
      const rootNode = n.getRootNode ? n.getRootNode() : null;
      n = rootNode && rootNode.host ? rootNode.host : null;
    }
    return blend(layers, pageBg);
  };
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
  const composedParent = (n) => {
    if (n.assignedSlot) return n.assignedSlot;
    const p = n.parentNode;
    return p && p.nodeType === 11 ? p.host || null : p;
  };
  let topLayer = null;
  const topLayerOf = () => {
    if (topLayer) return topLayer;
    const tops = new Set();
    const holders = new Set();
    const roots = [document];
    for (let i = 0; i < roots.length; i++) {
      for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
      for (const top of roots[i].querySelectorAll(':popover-open, :modal, :fullscreen')) {
        if (!top.checkVisibility({ checkVisibilityCSS: true })) continue;
        tops.add(top);
        for (let n = composedParent(top); n && n.nodeType === 1; n = composedParent(n)) {
          holders.add(n);
        }
      }
    }
    topLayer = { tops, holders, outside: new Map() };
    return topLayer;
  };
  const SILENT_TAGS = new Set(['script', 'style', 'template', 'noscript']);
  const textOutside = (layer, n) => {
    if (n.nodeType === 3) return !!n.data.trim();
    if (n.nodeType !== 1 || layer.tops.has(n) || SILENT_TAGS.has(n.localName)) return false;
    if (layer.outside.has(n)) return layer.outside.get(n);
    let found = false;
    for (const child of childrenOf(n)) if ((found = textOutside(layer, child))) break;
    layer.outside.set(n, found);
    return found;
  };
  const shieldsTopLayer = (el) => {
    const layer = topLayerOf();
    return layer.holders.has(el) && !textOutside(layer, el);
  };
  const TABLE_INTERNAL = new Set([
    'table-row', 'table-row-group', 'table-header-group', 'table-footer-group',
    'table-column', 'table-column-group', 'table-cell', 'table-caption',
  ]);
  const OVERFLOW_BOXES = new Set([
    'block', 'flow-root', 'inline-block', 'list-item', 'flex', 'inline-flex', 'grid', 'inline-grid',
  ]);
  const ALPHA_KEEPING_FILTERS = new Set([
    'opacity', 'blur', 'brightness', 'contrast', 'grayscale', 'hue-rotate', 'invert', 'saturate',
    'sepia', 'drop-shadow',
  ]);
  const isZero = (t) => t === '0px' || t === '0%';
  const percentOf = (t) => {
    if (t === '0px') return 0;
    return /^-?\d*\.?\d+(e[+-]?\d+)?%$/.test(t) ? parseFloat(t) : null;
  };
  const leastOf = (v) => (v === 0 ? 0 : v - 0.5 * 10 ** (Math.floor(Math.log10(Math.abs(v))) - 5));
  const emptyClipPath = (value) => {
    let m = /^(circle|ellipse)\((.*)\)$/.exec(value);
    if (m) return m[2].split(/\s*\bat\b/)[0].trim().split(/\s+/).some(isZero);
    m = /^polygon\((?:(?:nonzero|evenodd),\s*)?(.*)\)$/.exec(value);
    if (m) {
      const points = m[1].split(',').map((p) => p.trim().split(/\s+/));
      if (points.some((p) => p.length !== 2)) return false;
      return points.every((p) => isZero(p[0])) || points.every((p) => isZero(p[1]));
    }
    m = /^inset\((.*)\)$/.exec(value);
    if (!m) return false;
    const sides = m[1].split(/\s+round\s+/)[0].trim().split(/\s+/).map(percentOf);
    if (!sides.length || sides.length > 4 || sides.some((v) => v === null)) return false;
    const [t, r = t, b = t, l = r] = sides;
    return leastOf(t) + leastOf(b) >= 100 || leastOf(l) + leastOf(r) >= 100;
  };
  const FILTER_FUNCTION = /[a-z-]+\((?:[^()]|\([^()]*\))*\)/g;
  const filterAlpha = (value) => {
    const fns = value.match(FILTER_FUNCTION) || [];
    if (value.replace(FILTER_FUNCTION, '').trim()) return 1;
    let alpha = 1;
    for (const f of fns) {
      const name = f.slice(0, f.indexOf('('));
      if (!ALPHA_KEEPING_FILTERS.has(name)) return 1;
      if (name !== 'opacity') continue;
      const arg = f.slice(name.length + 1, -1).trim();
      const v = arg.endsWith('%') ? parseFloat(arg) / 100 : parseFloat(arg);
      if (Number.isNaN(v)) return 1;
      alpha *= v;
    }
    return alpha;
  };
  const collapsedTransform = (cs) => {
    if (cs.display === 'inline' || cs.transformStyle !== 'flat') return false;
    const [x, y = x] = cs.scale.split(/\s+/);
    if (x === '0' || y === '0') return true;
    const m = /^matrix\((.*)\)$/.exec(cs.transform);
    if (!m) return false;
    const [a, b, c, d] = m[1].split(/,\s*/);
    return (a === '0' && b === '0') || (c === '0' && d === '0');
  };
  const generatedPositions = (n) => ['::before', '::after']
    .map((pseudo) => getComputedStyle(n, pseudo))
    .filter((ps) => ps.content !== 'none' && ps.content !== 'normal')
    .map((ps) => ps.position);
  const escapeMemo = new Map();
  const positionsWithin = (n) => {
    if (escapeMemo.has(n)) return escapeMemo.get(n);
    const found = new Set([getComputedStyle(n).position, ...generatedPositions(n)]);
    for (const child of childrenOf(n)) {
      if (child.nodeType === 1) for (const p of positionsWithin(child)) found.add(p);
    }
    escapeMemo.set(n, found);
    return found;
  };
  const escapesClip = (el, cs) => {
    const found = new Set(generatedPositions(el));
    for (const child of childrenOf(el)) {
      if (child.nodeType === 1) for (const p of positionsWithin(child)) found.add(p);
    }
    return found.has('fixed') || (found.has('absolute') && cs.position === 'static');
  };
  const clipsToNothing = (cs) => {
    const clips = (o) => o === 'hidden' || o === 'auto' || o === 'scroll'
      || (o === 'clip' && cs.overflowClipMargin === '0px');
    const flatY = cs.height === '0px' && cs.paddingTop === '0px' && cs.paddingBottom === '0px';
    const flatX = cs.width === '0px' && cs.paddingLeft === '0px' && cs.paddingRight === '0px';
    return OVERFLOW_BOXES.has(cs.display) && cs.filter === 'none'
      && ((clips(cs.overflowY) && flatY) || (clips(cs.overflowX) && flatX));
  };
  const paintedAway = (el, cs) => {
    if (el === document.body || el === document.documentElement) return null;
    if (TABLE_INTERNAL.has(cs.display)) return null;
    if (emptyClipPath(cs.clipPath)) return 'clip-path';
    if (filterAlpha(cs.filter) <= ALPHA_THRESHOLD) return 'filter-opacity';
    if (collapsedTransform(cs)) return 'zero-scale';
    if (clipsToNothing(cs) && !escapesClip(el, cs)) return 'overflow-clipped';
    return null;
  };
  const hidingReason = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden')
      return 'display:none/visibility:hidden';
    if (cs.contentVisibility === 'hidden') return 'content-visibility:hidden';
    const noBox = cs.display === 'contents';
    const optionLike = (el.localName === 'option' || el.localName === 'optgroup')
      && !!(el.closest && el.closest('select'));
    if (!noBox && parseFloat(cs.opacity) <= ALPHA_THRESHOLD)
      return 'opacity<=' + ALPHA_THRESHOLD;
    if (ownsText(el) && parseFloat(cs.fontSize) < 4) return 'font-size<4px';
    const r = el.getBoundingClientRect();
    if (optionLike) {
    } else if (noBox) {
      if (!rendersContent(el)) return 'no-rendered-content';
    } else {
      if (r.width === 0 && r.height === 0 && el.getClientRects().length === 0) return 'zero-size';
      const w = parseFloat(cs.width);
      const h = parseFloat(cs.height);
      if (w <= TINY_BOX_PX && h <= TINY_BOX_PX) return 'sr-only-1px';
      const zeroRect = /rect\(\s*0[a-z%]*[\s,]+0[a-z%]*[\s,]+0[a-z%]*[\s,]+0[a-z%]*\s*\)/;
      if (cs.clip && zeroRect.test(cs.clip)) return 'clip-zero-rect';
    }
    if (!optionLike && r.width > 0 && r.height > 0) {
      const docW = document.documentElement.scrollWidth;
      const docH = document.documentElement.scrollHeight;
      if (r.right <= 0 || r.bottom <= 0 || r.left >= docW || r.top >= docH) return 'off-screen';
    }
    const fg = ownsText(el) ? parseColor(cs.color) : null;
    if (fg) {
      if (fg.a <= ALPHA_THRESHOLD) return 'text-color-transparent';
      const bg = effectiveBg(el);
      const l1 = relLuminance(fg);
      const l2 = relLuminance(bg);
      const contrast = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      if (contrast < CONTRAST_THRESHOLD) return 'color-contrast<' + CONTRAST_THRESHOLD;
    }
    if (!noBox && !optionLike && el.checkVisibility && !el.checkVisibility())
      return 'not-rendered';
    if (!noBox && !optionLike && el.getClientRects().length) return paintedAway(el, cs);
    return null;
  };
  const isHidden = (el) => {
    const reason = hidingReason(el);
    return reason && shieldsTopLayer(el) ? null : reason;
  };
  const root = document.body || document.documentElement;
  const toRemove = [];
  const shadowHidden = new Set();
  const sourceOnlyFlags = new Set();
  const attributeEdits = new Map();
  const lightHosts = [];
  let rootFlagged = null;
  const scan = (scopeRoot, inShadow, hosts) => {
    const walker = document.createTreeWalker(scopeRoot, NodeFilter.SHOW_ELEMENT);
    const flagged = !inShadow && rootFlagged ? [rootFlagged] : [];
    const insideFlagged = (el) => rootFlagged !== null || flagged.some((p) => p.contains(el));
    const nested = [];
    while (walker.nextNode()) {
      const el = walker.currentNode;
      if (el.shadowRoot) { hosts.push(el); nested.push(el); }
      if (el.tagName === 'SLOT' && el.assignedNodes) {
        const assigned = el.assignedNodes({ flatten: true });
        const slotReason = assigned.length ? isHidden(el) : null;
        if (slotReason) {
          const projected = assigned.map((n) => n.textContent || '').join('').trim();
          if (projected && !insideFlagged(el)) {
            report({
              text: projected.slice(0, MAX_TEXT), reason: slotReason, path: pathOf(el),
            });
          }
          for (const node of assigned) shadowHidden.add(node);
        }
      }
      const shadowText = el.shadowRoot ? (el.shadowRoot.textContent || '').trim() : '';
      const text = (el.textContent || '').trim() || shadowText;
      if (!text) continue;
      if (insideFlagged(el)) continue;
      const reason = isHidden(el);
      if (reason) {
        const sourceOnly =
          (el.localName === 'script' || el.localName === 'style') &&
          (el.namespaceURI === SVG_NS || getComputedStyle(el).display === 'none');
        if (!sourceOnly) {
          report({ text: text.slice(0, MAX_TEXT), reason, path: pathOf(el) });
        } else {
          sourceOnlyFlags.add(el);
        }
        flagged.push(el);
        if (inShadow) shadowHidden.add(el); else toRemove.push(el);
      }
    }
    const carrierRoot = inShadow ? scopeRoot : document.documentElement;
    const scopes = [{ node: carrierRoot, owner: null }];
    const templates = [];
    for (let i = 0; i < scopes.length; i++) {
      for (const tpl of scopes[i].node.querySelectorAll('template')) {
        if (!tpl.content) continue;
        const guard = scopes[i].owner || tpl;
        templates.push({ tpl, guard });
        scopes.push({ node: tpl.content, owner: guard });
      }
    }
    const carriers = new Map();
    if (carrierRoot.nodeType === 1) carriers.set(carrierRoot, carrierRoot);
    for (const scope of scopes) {
      for (const el of scope.node.querySelectorAll(ATTRIBUTE_CARRIER_SELECTOR)) {
        if (!carriers.has(el)) carriers.set(el, scope.owner || el);
      }
    }
    for (const [el, guard] of carriers) {
      const rebuiltFromLive = strip && (inShadow || lightHosts.some((h) => h.contains(el)));
      for (const name of TEXT_ATTRIBUTES) {
        const value = (el.getAttribute(name) || '').trim();
        if (!value) continue;
        if (rebuiltFromLive) {
          const names = attributeEdits.get(el);
          if (names) names.push(name); else attributeEdits.set(el, [name]);
        }
        if (value.length < MIN_ATTRIBUTE_CHARS) continue;
        if (insideFlagged(guard)) continue;
        report({
          text: value.slice(0, MAX_TEXT), reason: 'attribute:' + name, path: pathOf(el),
        });
      }
    }
    for (const { tpl, guard } of templates) {
      const text = (tpl.content.textContent || '').trim();
      if (text.length < MIN_COMMENT_CHARS) continue;
      if (insideFlagged(guard)) continue;
      report({ text: text.slice(0, MAX_TEXT), reason: 'template', path: pathOf(tpl) });
    }
    for (const host of nested) scan(host.shadowRoot, true, []);
  };
  if (root) {
    const rootReason = isHidden(root);
    if (rootReason) {
      const rootText = (root.textContent || '').trim();
      if (rootText) {
        report({ text: rootText.slice(0, MAX_TEXT), reason: rootReason, path: pathOf(root) });
      }
      rootFlagged = root;
      toRemove.push(root);
    }
  }
  scan(root, false, lightHosts);
  const hiddenPaths = toRemove.map(indexPathOf);
  const hiddenSelectors = strip ? toRemove.map(selectorOf) : [];
  const bodyPath = document.body ? indexPathOf(document.body) : null;
  const commentWalker = document.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
  const commentPaths = [];
  while (commentWalker.nextNode()) {
    const c = commentWalker.currentNode;
    const text = (c.textContent || '').trim();
    if (text.length < MIN_COMMENT_CHARS) continue;
    report({
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
  const title = document.title;
  let stripIncomplete = false;
  let html;
  let copy = null;
  const carriesText = (el) => !sourceOnlyFlags.has(el);
  const unhiding =
    !strip && (toRemove.some(carriesText) || Array.from(shadowHidden).some(carriesText));
  const flaggedLive = new Set(toRemove);
  const clearHidingMarkers = (el) => {
    for (const name of HIDING_ATTRIBUTES) el.removeAttribute(name);
    const classes = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean);
    const kept = classes.filter((name) => !HIDING_NAME.test(name));
    if (kept.length !== classes.length) {
      if (kept.length) el.setAttribute('class', kept.join(' '));
      else el.removeAttribute('class');
    }
    if (HIDING_NAME.test(el.getAttribute('id') || '')) el.removeAttribute('id');
  };
  const unhide = (el) => {
    clearHidingMarkers(el);
    for (const inner of el.querySelectorAll('*')) clearHidingMarkers(inner);
  };
  if (strip || lightHosts.length || unhiding) {
    const inert = document.implementation.createHTMLDocument('');
    copy = inert.importNode(document.documentElement, true);
    const doomed = hiddenPaths.concat(commentPaths).map((parts) => atIndexPath(copy, parts));
    const hostPairs = lightHosts.map((h) => [h, atIndexPath(copy, indexPathOf(h))]);
    if (strip) {
      for (const name of TEXT_ATTRIBUTES) copy.removeAttribute(name);
      for (const el of copy.querySelectorAll(ATTRIBUTE_CARRIER_SELECTOR)) {
        for (const name of TEXT_ATTRIBUTES) el.removeAttribute(name);
      }
      for (const tpl of copy.querySelectorAll('template')) {
        const frag = tpl.content;
        if (!frag) continue;
        while (frag.firstChild) frag.removeChild(frag.firstChild);
      }
    }
    for (const node of doomed) {
      if (!node) { stripIncomplete = true; continue; }
      if (strip) node.remove(); else unhide(node);
    }
    const dropped = new Set();
    if (strip) {
      for (const node of shadowHidden) dropped.add(node);
      for (const node of toRemove) dropped.add(node);
    }
    const composed = (live, insideKept = false) => {
      if (dropped.has(live)) return null;
      if (live.nodeType === 3) return inert.createTextNode(live.data);
      if (live.nodeType !== 1) return null;
      const el = inert.importNode(live, false);
      const kept =
        insideKept || (unhiding && (shadowHidden.has(live) || flaggedLive.has(live)));
      if (kept) clearHidingMarkers(el);
      const edits = attributeEdits.get(live);
      if (edits) for (const name of edits) el.removeAttribute(name);
      for (const child of childrenOf(live)) {
        const built = composed(child, kept);
        if (built) el.appendChild(built);
      }
      return el;
    };
    for (const [live, copyHost] of hostPairs) {
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

  let adopted = null;
  if (strip && hiddenSelectors.length && 'adoptedStyleSheets' in Document.prototype) {
    adopted = new CSSStyleSheet();
    adopted.replaceSync(hiddenSelectors.join(',') + '{display:none !important}');
    const sheets = Array.prototype.slice.call(document.adoptedStyleSheets);
    sheets.push(adopted);
    document.adoptedStyleSheets = sheets;
  }
  let text = document.body ? document.body.innerText : '';
  const BREAKS_LINE =
    'address,article,aside,blockquote,br,caption,center,dd,details,dialog,div,dl,dt,' +
    'fieldset,figcaption,figure,footer,form,h1,h2,h3,h4,h5,h6,header,hgroup,hr,legend,' +
    'li,main,menu,nav,ol,p,pre,search,section,summary,table,tbody,td,tfoot,th,thead,tr,ul';
  if ((strip || lightHosts.length || unhiding) && document.body) {
    let untrusted = unhiding || document.body.getClientRects().length === 0;
    if (!untrusted) {
      for (const el of toRemove) {
        if (el.localName === 'option' || el.localName === 'optgroup') {
          untrusted = true;
          break;
        }
        if (getComputedStyle(el).display !== 'none') { untrusted = true; break; }
      }
    }
    if (untrusted || lightHosts.length) {
      if (strip) stripIncomplete = true;
      const noise = copy.querySelectorAll('script,style,noscript,template');
      for (const el of Array.prototype.slice.call(noise)) el.remove();
      const breaks = copy.querySelectorAll(BREAKS_LINE);
      for (const el of Array.prototype.slice.call(breaks)) {
        el.appendChild(copy.ownerDocument.createTextNode('\n'));
      }
      const body = bodyPath ? atIndexPath(copy, bodyPath) : null;
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
    spansDropped,
  };
}
"""
