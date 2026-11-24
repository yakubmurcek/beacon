'use strict';
// Knowledge: everything the agents know, for him to read. Search runs over the corpus (mail, iMessage,
// Claude chats, calls, Hermes), the ai-brain notes and gbrain at once; Notes shows the brain as cards;
// Calls lists every recorded call, opened by speaker. Reads /api/knowledge/* (hub/knowledge.py).
// Every text from the corpus goes in as text nodes, never as HTML: mail bodies carry raw markup.

const SRC = {
  'icloud-mail': { label: 'Mail', color: 'var(--s1)' },
  imessage: { label: 'iMessage', color: 'var(--s3)' },
  'claude-sessions': { label: 'Claude chats', color: 'var(--s2)' },
  transcripts: { label: 'Calls', color: 'var(--s7)' },
  hermes: { label: 'Hermes bot', color: 'var(--s4)' },
  brain: { label: 'Brain notes', color: 'var(--accent)' },
  gbrain: { label: 'gbrain', color: 'var(--s5)' },
};
const ORDER = ['brain', 'icloud-mail', 'imessage', 'claude-sessions', 'transcripts', 'hermes', 'gbrain'];
const SECTIONS = { core: 'Core', feedback: 'Feedback', reference: 'Reference' };
const SPEAKER = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s7)', 'var(--s4)', 'var(--s5)', 'var(--s6)', 'var(--s8)'];
const PAGE = 24;  // cards before "Show more": short lists, no endless scroll
const LANE = 6;  // search hits per kind before "Show all"
const openLanes = new Set();

let overview = snap.get('kn.overview');
let notes = snap.get('kn.notes');
let calls = snap.get('kn.calls');
let results = null;  // {key, data} of the last search
let more = 1;  // how many pages of cards are open
let filter = '';  // the search box, on Notes and Calls: narrows the cards as he types

// ------------------------------------------------------------------ time and words

const TZ = 'Europe/Prague';
const dayKey = (d) => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(d);
function when(ts, withTime) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const k = dayKey(d), today = pragueToday(), y = pragueToday(-1);
  const time = new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }).format(d);
  if (k === today) return 'today ' + time;
  if (k === y) return 'yesterday ' + time;
  const sameYear = k.slice(0, 4) === today.slice(0, 4);
  const date = new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { timeZone: TZ, day: 'numeric', month: 'short', year: sameYear ? undefined : 'numeric' }).format(d);
  return withTime ? `${date}, ${time}` : date;
}
const num = (x) => Number(x || 0).toLocaleString('en-US');  // not `n`: the header's inline script owns that global
const mmss = (s) => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0'); };
// Accents and case off, one character for one, so a match's index is the same in the original text.
const fold = (s) => Array.from(String(s || '')).map((c) => {
  const f = c.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
  return f.length === c.length ? f : c;
}).join('');
const words = (q) => (q || '').toLowerCase().replace(/\*/g, '').split(/[^\p{L}\p{N}@.+-]+/u).filter((w) => w && w !== 'or' && w !== 'and').map(fold);

/** Text with the search words marked, as nodes (never HTML). Accents don't matter: plevak finds Plevák. */
function marked(text, ws) {
  text = String(text || '');
  if (!ws || !ws.length) return [text];
  const low = fold(text);
  const spans = [];
  for (const w of ws) { let i = 0; while (w.length > 1 && (i = low.indexOf(w, i)) >= 0) { spans.push([i, i + w.length]); i += w.length; } }
  if (!spans.length) return [text];
  spans.sort((a, b) => a[0] - b[0]);
  const out = []; let at = 0;
  for (const [a, b] of spans) {
    if (a < at) continue;
    out.push(text.slice(at, a), h('mark', {}, text.slice(a, b)));
    at = b;
  }
  out.push(text.slice(at));
  return out;
}

function dot(src) { return h('i', { class: 'src-dot', style: `background:${(SRC[src] || {}).color || 'var(--other)'}` }); }
function srcLabel(src) { return (SRC[src] || { label: src }).label; }

// ------------------------------------------------------------------ markdown for brain notes and gbrain pages

const RICH = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]|\[([^\]\n]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<>"'()]*[^\s<>"'().,;:!?])|`([^`]+)`|\*\*([^*]+)\*\*/g;

function noteHref(target, from) {
  // [[name]], [[knowledge/x/y|label]], [t](relative.md): the note it names, if the brain has it
  let t;
  try { t = decodeURIComponent(target); } catch { t = target; }  // [[100% done]] is a name, not an encoding
  t = t.replace(/#.*$/, '').replace(/\.md$/, '');
  if (!notes) return null;
  const list = notes.notes || [];
  const dir = (from || '').split('/').slice(0, -1);
  const tries = [];
  if (t.startsWith('/')) tries.push(t.slice(1));
  else {
    const parts = [...dir];
    for (const p of t.split('/')) { if (p === '..') parts.pop(); else if (p !== '.') parts.push(p); }
    tries.push(parts.join('/'), t);
  }
  for (const p of tries) { const hit = list.find((x) => x.path === p + '.md' || x.path === p + '/README.md'); if (hit) return '#/n/' + encodeURIComponent(hit.path); }
  const name = t.split('/').pop().toLowerCase();
  const hit = list.find((x) => x.name.toLowerCase() === name)  // the graph (resolve_note in hub/knowledge.py) resolves the same way
    || list.find((x) => x.name.toLowerCase() === 'readme' && x.path.split('/').length > 2 && x.path.split('/').slice(-2, -1)[0].toLowerCase() === name);  // [[folder]]: its README
  return hit ? '#/n/' + encodeURIComponent(hit.path) : name === 'index' ? '#/n/index.md' : null;
}

function richInline(raw, from) {
  let out = '', last = 0, m;
  const re = new RegExp(RICH.source, 'g');  // its own cursor: bold text recurses into this function
  while ((m = re.exec(raw))) {
    out += styled(esc(raw.slice(last, m.index)));
    last = re.lastIndex;
    if (m[1]) {
      const href = noteHref(m[1], from), label = esc(m[2] || m[1].split('/').pop().replace(/-/g, ' '));
      out += href ? `<a class="wl" href="${esc(href)}">${label}</a>` : `<span class="wl dead">${label}</span>`;
    } else if (m[3]) {
      const url = m[4];
      if (/^https?:\/\//.test(url)) out += `<a href="${esc(url)}" target="_blank" rel="noopener">${styled(esc(m[3]))}</a>`;
      else { const href = noteHref(url, from); out += href ? `<a class="wl" href="${esc(href)}">${styled(esc(m[3]))}</a>` : styled(esc(m[3])); }
    } else if (m[5]) out += `<a href="${esc(m[5])}" target="_blank" rel="noopener">${esc(m[5])}</a>`;
    else if (m[6]) out += `<code>${esc(m[6])}</code>`;
    else if (m[7]) out += `<strong>${richInline(m[7], from)}</strong>`;
  }
  return out + styled(esc(raw.slice(last)));
}

function noteHtml(src, from) {
  const lines = String(src || '').split('\n');
  const out = [];
  let para = [], list = null, code = false, table = null, quote = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${richInline(para.join(' '), from)}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const flushQuote = () => { if (quote.length) { out.push(`<blockquote>${richInline(quote.join(' '), from)}</blockquote>`); quote = []; } };
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const flushTable = () => {
    if (!table) return;
    const [head, ...rows] = table.filter((r) => !r.every((c) => /^:?-{2,}:?$/.test(c)));
    out.push('<div class="tbl"><table><thead><tr>' + head.map((c) => `<th>${richInline(c, from)}</th>`).join('') + '</tr></thead><tbody>' +
      rows.map((r) => '<tr>' + r.map((c) => `<td>${richInline(c, from)}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>');
    table = null;
  };
  const flush = () => { flushPara(); flushList(); flushQuote(); flushTable(); };
  for (const line of lines) {
    if (line.trim().startsWith('```')) { flush(); out.push(code ? '</code></pre>' : '<pre><code>'); code = !code; continue; }
    if (code) { out.push(esc(line) + '\n'); continue; }
    let m;
    if (/^\s*\|.*\|\s*$/.test(line)) { flushPara(); flushList(); flushQuote(); (table = table || []).push(cells(line)); continue; }
    if (table) flushTable();
    if ((m = line.match(/^(#{1,6})\s+(.*)/))) { flush(); const lv = Math.min(4, m[1].length + 1); out.push(`<h${lv}>${richInline(m[2], from)}</h${lv}>`); continue; }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { flush(); out.push('<hr>'); continue; }
    if ((m = line.match(/^\s*>\s?(.*)/))) { flushPara(); flushList(); quote.push(m[1]); continue; }
    if ((m = line.match(/^(\s*)[-*+]\s+(.*)/)) || (m = line.match(/^(\s*)\d+[.)]\s+(.*)/))) {
      flushPara(); flushQuote();
      const kind = /^\s*\d/.test(line) ? 'ol' : 'ul';
      if (list !== kind) { flushList(); out.push(`<${kind}>`); list = kind; }
      const box = m[2].match(/^\[( |x)\]\s+(.*)/i);
      out.push(`<li${m[1].length >= 2 ? ' class="sub"' : ''}>${box ? (box[1].trim() ? '☑ ' : '☐ ') + richInline(box[2], from) : richInline(m[2], from)}</li>`);
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    if (list && /^\s{2,}\S/.test(line)) {  // a list item's next line; sliced, never .replace(): $` in a note would expand
      const last = out[out.length - 1];
      out[out.length - 1] = last.slice(0, -5) + ' ' + richInline(line.trim(), from) + '</li>';
      continue;
    }
    flushList(); flushQuote();
    para.push(line.trim());
  }
  flush();
  if (code) out.push('</code></pre>');
  return out.join('');
}

// ------------------------------------------------------------------ routes

function route() {
  const [path, qs] = location.hash.replace(/^#\/?/, '').split('?');
  const p = new URLSearchParams(qs || '');
  const [view, ...rest] = path.split('/');
  let id = rest.length ? rest.join('/') : null;
  try { id = id && decodeURIComponent(id); } catch { /* a broken link: use it as written */ }
  return { view: view || 'search', id, q: p.get('q') || '', source: p.get('s') || '', at: p.get('at') || '' };
}
const go = (hash) => { if (location.hash !== hash) location.hash = hash; else render(); };
const searchHash = (q, s) => '#/?' + new URLSearchParams(Object.entries({ q, s }).filter(([, v]) => v)).toString();

// ------------------------------------------------------------------ graph: the brain drawn the way Obsidian draws it
// force-graph 1.52.0 (MIT, github.com/vasturiano/force-graph), vendored in web/vendor: canvas, pan, pinch, drag.
// One graph lives for the whole visit: the layout is worked out off screen (in slices, so a phone never
// freezes), remembered per dot, and the canvas fades in once, already fitted. New data moves the same dots
// to their new places; nothing is torn down, so a reload or a refresh never blinks or re-lays the brain.
// Hover (a finger: tap) lights the dot and its links, fades the rest and draws the neighbours in a little;
// dots near the pointer lean towards it. All of that is drawn, never simulated, so the brain stays put.

const GROUPS = {  // drawn straight on the page: greys for the many, a few strong colours for what matters
  core: { label: 'Core notes', color: '#e07b2a' },
  decisions: { label: 'Decisions', color: '#c9a20c' },
  reference: { label: 'Notes', color: '#d8453c' },
  feedback: { label: 'Rules', color: '#9a8a1e' },
  project: { label: 'Projects', color: null },  // the page's ink, so it reads on both themes
  chat: { label: 'Claude chats', color: '#9a9283' },
  call: { label: 'Calls', color: '#5b5bd6' },
  person: { label: 'People on calls', color: '#8f9cf0' },
  missing: { label: 'Not written yet', color: '#c9c0ae' },
};
// the page's own ink and paper, read from the theme so the cyber theme draws right too
let ink = '#17150F', paper = '#F3EFE6';
function readTheme() {
  const cs = getComputedStyle(document.body);
  ink = cs.getPropertyValue('--text').trim() || ink;
  paper = cs.getPropertyValue('--bg').trim() || paper;
}
const rgb = (c) => { const m = /^#([0-9a-f]{6})$/i.exec(c); if (!m) return [100, 116, 139]; const v = parseInt(m[1], 16); return [v >> 16, (v >> 8) & 255, v & 255]; };
const rgba = (c, a) => { const [r, g, b] = rgb(c); return `rgba(${r},${g},${b},${a})`; };
const colorOf = (x) => (GROUPS[x.group] && GROUPS[x.group].color) || ink;
const TOUCH = matchMedia('(hover: none)').matches;
const CALM = matchMedia('(prefers-reduced-motion: reduce)').matches;
const radius = (x) => 2 + Math.sqrt(x.deg);  // Quartz's (and Obsidian's) dot size: 2 + sqrt(links)
const POS = 'kn.graphpos';  // where every dot was last drawn, so the next visit starts there

let graph = snap.get('kn.graph');
let fg = null, gbox = null, canvasBox = null, gsig = '', shown = false, revealed = false, userMoved = false;
let hover = null, picked = null, matches = null;  // what lights up: the dot under the pointer, the tapped one, search hits
let pointer = null;  // the mouse, in graph units
let layoutJob = 0, tween = null, lastFrame = 0, awake = false;
const hiddenGroups = new Set();
const nbrs = new Map();
let byId = new Map();

function graphHref(x) {
  if (x.id.startsWith('call:')) return '#/call/' + encodeURIComponent(x.id.slice(5));
  if (x.id.startsWith('chat:')) return '/usage#/session/' + encodeURIComponent(x.id.slice(5));
  if (x.id.startsWith('project:')) return searchHash(x.label, 'claude-sessions');
  if (x.id.startsWith('person:')) return searchHash(x.label, 'transcripts');
  if (x.id.startsWith('missing:')) return searchHash(x.label, '');
  return '#/n/' + encodeURIComponent(x.id);
}

const focusNode = () => hover || picked;
function lit() {  // ids drawn bright; null = everything
  const x = focusNode();
  if (x) return new Set([x.id, ...(nbrs.get(x.id) || [])]);
  return matches;
}

// ---- the frame: every dot eases towards its target look; while anything still moves, redraw every frame

function wake() {  // something changed: draw every frame until the dots have eased into place
  if (!fg) return;
  if (!awake) { awake = true; lastFrame = 0; fg.autoPauseRedraw(false); }
}

function tweenStep(now) {  // fresh data: known dots glide to their new places
  const t = Math.min(1, (now - tween.start) / 650);
  const e = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;  // ease in-out
  for (const [x, a, b] of tween.moves) { x.x = a[0] + (b[0] - a[0]) * e; x.y = a[1] + (b[1] - a[1]) * e; }
  if (t === 1) { tween = null; savePositions(); }
}

function lean(x, f, on, pull, scale) {  // where a dot is drawn off its place: [dx, dy]
  let tx = 0, ty = 0;
  if (f && x !== f && on.has(x.id)) { tx += (f.x - x.x) * 0.1; ty += (f.y - x.y) * 0.1; }  // the neighbours gather in round the lit dot
  if (!pull) return [tx, ty];
  const ddx = pointer.x - x.x, ddy = pointer.y - x.y, d = Math.hypot(ddx, ddy);  // and dots near the pointer lean towards it
  if (d >= pull || d < 0.01) return [tx, ty];
  const w = Math.min((1 - d / pull) ** 2 * (12 / scale), d);
  return [tx + (ddx / d) * w, ty + (ddy / d) * w];
}

function looks(x, f, on) {  // a dot's target brightness, glow and size
  const g = f ? (x === f ? 1 : on.has(x.id) ? 0.5 : 0) : matches && matches.has(x.id) ? 0.8 : 0;
  return { b: !on || on.has(x.id) ? 1 : 0.2, g, s: x === f ? 1.6 : g > 0 ? 1.2 : 1 };
}

function step(scale) {
  const now = performance.now();
  const dt = lastFrame ? Math.min(64, now - lastFrame) : 16;
  lastFrame = now;
  const k = CALM ? 1 : 1 - 0.8 ** (dt / 16.7);  // ~0.2 a frame at 60 fps, the same speed at 120
  const f = focusNode();
  const on = lit();
  const pull = pointer && !TOUCH && !CALM ? 90 / scale : 0;  // the pointer's reach: 90 screen px
  let moving = !!f || !!tween;  // the lit dot breathes while it is lit
  if (tween) tweenStep(now);
  const ease = (cur, tgt) => { const v = cur + (tgt - cur) * k; if (Math.abs(tgt - v) > 0.004) moving = true; return Math.abs(tgt - v) > 0.0005 ? v : tgt; };
  for (const x of fg.graphData().nodes) {
    const want = looks(x, f, on);
    const [tx, ty] = lean(x, f, on, pull, scale);
    x.b = ease(x.b ?? 1, want.b);
    x.g = ease(x.g ?? 0, want.g);
    x.s = ease(x.s ?? 1, want.s);
    x.dx = ease(x.dx || 0, tx);
    x.dy = ease(x.dy || 0, ty);
  }
  if (!moving && awake) { awake = false; fg.autoPauseRedraw(true); }
}

const px = (x) => x.x + (x.dx || 0);
const py = (x) => x.y + (x.dy || 0);

function drawNode(x, ctx, scale) {
  const X = px(x), Y = py(x), c = colorOf(x);
  const f = focusNode();
  const r = radius(x) * (x.s || 1);
  const b = x.b ?? 1;
  if (x.g > 0.02) {  // the glow: a soft halo in the dot's own colour, breathing on the lit one
    const breathe = x === f && !CALM ? 1 + 0.12 * Math.sin(performance.now() / 380) : 1;
    const R = Math.max(r * (1.8 + 1.6 * x.g), (x === f ? 18 : 9) * x.g / scale) * breathe;  // still a glow when zoomed out
    const grad = ctx.createRadialGradient(X, Y, r * 0.6, X, Y, R);
    grad.addColorStop(0, rgba(c, 0.42 * x.g));
    grad.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.arc(X, Y, R, 0, 2 * Math.PI); ctx.fill();
  }
  ctx.globalAlpha = b * (x.group === 'missing' ? 0.6 : x.deg ? 1 : 0.65);
  ctx.beginPath();
  ctx.arc(X, Y, r, 0, 2 * Math.PI);
  ctx.fillStyle = c;
  ctx.fill();
  if (x.g > 0.5) {  // a paper rim keeps a lit dot crisp on top of its own glow
    ctx.globalAlpha = Math.min(1, (x.g - 0.5) * 2.5);
    ctx.lineWidth = (x === f ? 2 : 1.2) / scale;
    ctx.strokeStyle = paper;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  label(x, ctx, scale, X, Y, r);
}

function labelFade(x, scale) {
  // names stay hidden until he zooms in and fade in as he does, as Quartz/Obsidian do;
  // the lit dot always (as a pill), its neighbours when there are few of them;
  // well-linked dots name themselves first, the many one-link chats only once he is close
  if (x === focusNode()) return Math.min(1, x.g * 1.4);
  const on = lit();
  const few = on && on.size <= 14 && on.has(x.id) ? x.g * 1.2 : 0;
  const zoomFade = Math.min(1, Math.max(0, (scale - (x.deg >= 3 ? 1.3 : 2.8)) / 2.5));
  return Math.min(1, Math.max(few, zoomFade * (x.b ?? 1) ** 2));
}

function pill(ctx, x, text, X, ty, scale) {  // the lit dot's name on a paper pill with a soft shadow
  const w = ctx.measureText(text).width + 16 / scale, hgt = 22 / scale;
  ctx.beginPath();
  ctx.roundRect(X - w / 2, ty - hgt / 2, w, hgt, hgt / 2);
  ctx.fillStyle = paper;
  ctx.shadowColor = 'rgba(0,0,0,0.18)';
  ctx.shadowBlur = 10;
  ctx.shadowOffsetY = 2;
  ctx.fill();
  ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
  ctx.lineWidth = 1 / scale;
  ctx.strokeStyle = rgba(colorOf(x), 0.55);
  ctx.stroke();
}

function label(x, ctx, scale, X, Y, r, top) {  // the lit dot's own name is drawn last (top), over every other dot
  const isFocus = x === focusNode();
  if (isFocus !== !!top) return;
  const fade = labelFade(x, scale);
  if (fade < 0.03) return;
  const text = x.label.length > 42 ? `${x.label.slice(0, 40)}…` : x.label;
  ctx.font = `${isFocus ? 600 : 500} ${(isFocus ? 13 : 11.5) / scale}px Inter, system-ui, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const ty = Y + r + (isFocus ? 14 : 9) / scale;
  ctx.globalAlpha = fade;
  if (isFocus) pill(ctx, x, text, X, ty, scale);
  else { ctx.lineWidth = 3 / scale; ctx.strokeStyle = paper; ctx.strokeText(text, X, ty); }
  ctx.fillStyle = ink;
  ctx.fillText(text, X, ty);
  ctx.globalAlpha = 1;
}

function drawLink(l, ctx, scale) {
  const s = l.source, t = l.target;
  if (typeof s !== 'object' || typeof t !== 'object') return;
  const f = focusNode();
  const hot = f && (s === f || t === f) ? (s === f ? t : s).g || 0 : 0;  // eases in with the far dot's glow
  const dim = Math.min(s.b ?? 1, t.b ?? 1);
  const sx = px(s), sy = py(s), tx = px(t), ty = py(t);
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.lineTo(tx, ty);
  if (hot > 0.02) {
    ctx.strokeStyle = rgba(colorOf(f), 0.2 + 0.6 * hot);
    ctx.lineWidth = (0.5 + 1.1 * hot) / scale;
  } else {
    ctx.strokeStyle = rgba(ink, 0.15 * (0.25 + 0.75 * dim));
    ctx.lineWidth = 0.5 / scale;
  }
  ctx.stroke();
  if (hot > 0.3 && !CALM) {  // a spark runs out from the lit dot along each of its links
    const from = s === f ? [sx, sy] : [tx, ty], to = s === f ? [tx, ty] : [sx, sy];
    const p = ((performance.now() + (l.seed || 0)) % 1400) / 1400;
    ctx.globalAlpha = hot * Math.sin(Math.PI * p);
    ctx.fillStyle = colorOf(f);
    ctx.beginPath();
    ctx.arc(from[0] + (to[0] - from[0]) * p, from[1] + (to[1] - from[1]) * p, 1.6 / scale, 0, 2 * Math.PI);
    ctx.fill();
    ctx.globalAlpha = 1;
  }
}

function hitArea(x, color, ctx, scale) {  // where the pointer finds a dot: where it is drawn, never smaller than 4 px
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(px(x), py(x), Math.max(radius(x) * (x.s || 1), 4 / scale), 0, 2 * Math.PI);
  ctx.fill();
}

// ---- the panel round the canvas

function pickBar() {
  const bar = gbox && gbox.querySelector('.kn-pick');
  if (!bar) return;
  const x = focusNode();
  bar.classList.toggle('on', !!x);
  if (!x) return;
  const card = notes && notes.notes ? notes.notes.find((c) => c.path === x.id) : null;
  const sub = card ? card.blurb : x.group === 'call' ? 'Call · ' + (x.changed ? when(x.changed) : '') : x.group === 'person' ? `On ${x.deg} call${x.deg === 1 ? '' : 's'}` : x.group === 'chat' ? `Claude chat · ${x.changed ? when(x.changed) : ''} · ${num(x.size)} messages` : x.group === 'project' ? `Project · ${x.deg} chats and notes round it` : x.group === 'missing' ? 'Linked to, but no note has this name yet' : '';
  const links = (nbrs.get(x.id) || new Set()).size;
  bar.replaceChildren(h('i', { style: `--c:${colorOf(x)}` }),
    h('span', { class: 'tx' }, h('span', { class: 't' }, x.label), h('span', { class: 'b' }, [(GROUPS[x.group] || { label: '' }).label, links ? `${links} link${links === 1 ? '' : 's'}` : '', sub].filter(Boolean).join(' · '))),
    h('a', { class: 'open', href: graphHref(x) }, ['person', 'missing', 'project'].includes(x.group) ? 'Search →' : 'Open →'));
}

function relight() { readTheme(); pickBar(); wake(); }

function legend() {
  const box = gbox.querySelector('.kn-legend');
  const counts = {};
  for (const x of graph.nodes) counts[x.group] = (counts[x.group] || 0) + 1;
  box.replaceChildren(
    ...Object.entries(GROUPS).filter(([k]) => counts[k]).map(([k, g]) => {
      const b = h('button', { type: 'button', class: hiddenGroups.has(k) ? 'off' : '', title: 'Show or hide ' + g.label },
        h('i', { style: `--c:${g.color || 'var(--text)'}` }), g.label, h('span', { class: 'count' }, String(counts[k])));
      b.addEventListener('click', () => {
        if (hiddenGroups.has(k)) hiddenGroups.delete(k); else hiddenGroups.add(k);
        b.classList.toggle('off', hiddenGroups.has(k));
        if (fg) fg.nodeVisibility((x) => !hiddenGroups.has(x.group)).linkVisibility((l) => !hiddenGroups.has(l.source.group) && !hiddenGroups.has(l.target.group));
      });
      return b;
    }),
    (() => { const b = h('button', { type: 'button', class: 'fit', title: 'Show the whole brain' }, 'Fit'); b.addEventListener('click', () => { picked = null; userMoved = false; relight(); if (fg) fg.zoomToFit(500, 30); }); return b; })());
}

function graphBox() {  // built once, above the view: render() only shows or hides it, so it is never re-attached
  if (gbox) return gbox;
  canvasBox = h('div', { class: 'kn-graph-canvas' });
  gbox = h('section', { class: 'kn-graph', 'aria-label': 'Graph of the brain notes', hidden: '' },
    canvasBox, h('div', { class: 'kn-legend' }), h('div', { class: 'kn-pick' }), h('p', { class: 'kn-graph-wait' }, 'Drawing the brain…'));
  $('view').before(gbox);
  return gbox;
}

function showGraph(on) {
  graphBox().hidden = !on;
  shown = on;
  if (!fg) return;
  if (on) { fg.resumeAnimation(); sizeCanvas(); reveal(); } else fg.pauseAnimation();
}

function sizeCanvas() {  // a width the canvas already has is never set again: setting it wipes the picture
  const w = canvasBox.clientWidth, hgt = canvasBox.clientHeight;
  if (!fg || !w || !hgt) return false;
  if (fg.width() !== w || fg.height() !== hgt) {
    fg.width(w).height(hgt);
    if (revealed && !userMoved && !picked) fg.zoomToFit(0, 30);
  }
  return true;
}

function reveal() {  // once a visit: fitted at once, then the canvas fades in while the camera eases in a touch
  if (revealed || !shown || layoutJob || !sizeCanvas()) return;
  revealed = true;
  fg.zoomToFit(0, 30);
  if (!CALM) { const k = fg.zoom(); fg.zoom(k * 0.92, 0); fg.zoom(k, 600); }
  gbox.classList.add('ready');
}

// ---- data in: the same graph gets the new dots; the layout runs off screen, in slices

function savePositions() {
  if (!fg) return;
  const out = {};
  for (const x of fg.graphData().nodes) if (Number.isFinite(x.x)) out[x.id] = [Math.round(x.x), Math.round(x.y)];
  out['#shape'] = hash(gsig);  // the dots and links these places were worked out for
  snap.set(POS, out);
}

let worker;
try { worker = new Worker('knowledge-layout.js'); } catch { worker = null; }
function settle(nodes, links, alpha, done) {  // knowledge-layout.js works the layout out in a worker
  const job = ++layoutJob;
  const finish = (places) => {
    if (job !== layoutJob) return;  // newer data took over
    layoutJob = 0;
    for (const [id, x, y] of places) at.set(id, [x, y]);
    done(at);
  };
  const at = new Map();
  if (worker) {
    worker.onmessage = (e) => finish(e.data.places);
    worker.onerror = () => { worker = null; settle(nodes, links, alpha, done); };
    worker.postMessage({ job, nodes, links, alpha });
    return;
  }
  // no worker (blocked): the same forces on the page, ~10 ms a frame so it never freezes
  const sim = d3.forceSimulation(nodes)
    .alpha(alpha).alphaMin(0.004).alphaDecay(0.045)
    .force('charge', d3.forceManyBody().strength(-50).distanceMax(400))
    .force('link', d3.forceLink(links).id((x) => x.id).distance(30))
    .force('collide', d3.forceCollide((x) => radius(x) + 1))
    .force('x', d3.forceX(0).strength(0.04))
    .force('y', d3.forceY(0).strength(0.04))
    .stop();
  const slice = () => {
    if (job !== layoutJob) return;
    const t0 = performance.now();
    while (sim.alpha() > sim.alphaMin() && performance.now() - t0 < 10) sim.tick();
    if (sim.alpha() > sim.alphaMin()) return requestAnimationFrame(slice);
    finish(nodes.map((x) => [x.id, x.x, x.y]));
  };
  requestAnimationFrame(slice);
}

function linkUp(g) {  // who is linked to whom, both ways
  nbrs.clear();
  for (const l of g.links) {
    if (!nbrs.has(l.source)) nbrs.set(l.source, new Set());
    if (!nbrs.has(l.target)) nbrs.set(l.target, new Set());
    nbrs.get(l.source).add(l.target);
    nbrs.get(l.target).add(l.source);
  }
}

function startPlaces(g, prev, saved) {  // every dot starts where it was: this visit, else the last visit, else by a neighbour
  const start = new Map();
  for (const x of g.nodes) {
    const o = prev.get(x.id);
    if (o) start.set(x.id, [o.x, o.y]);
    else if (saved[x.id]) start.set(x.id, saved[x.id]);
  }
  for (const x of g.nodes) {
    if (start.has(x.id)) continue;
    const nb = [...(nbrs.get(x.id) || [])].find((y) => start.has(y));
    if (nb) { const p = start.get(nb); start.set(x.id, [p[0] + (Math.random() - 0.5) * 20, p[1] + (Math.random() - 0.5) * 20]); }
  }
  return start;
}

function graphReady(g) {  // false (and says why on the canvas) when there is nothing to draw yet
  const wait = graphBox().querySelector('.kn-graph-wait');
  if (!g || !g.nodes) { wait.textContent = g && g.error ? `The graph is not reachable: ${g.error}` : 'Drawing the brain…'; return false; }
  if (typeof ForceGraph === 'undefined' || typeof d3 === 'undefined') { wait.textContent = 'The graph script did not load.'; return false; }
  return true;
}

function setGraph(g) {
  graph = g;
  if (!graphReady(g)) return;
  legend();
  linkUp(g);
  const sig = `${g.nodes.map((x) => x.id).join('|')}#${g.links.map((l) => `${l.source}>${l.target}`).join('|')}`;
  if (fg && sig === gsig) {  // the same brain: new sizes and names only, drawn in place
    for (const x of g.nodes) { const o = byId.get(x.id); if (o) Object.assign(o, x); }
    pickBar(); wake();
    return;
  }
  gsig = sig;
  const saved = snap.get(POS) || {};
  const prev = byId;
  const start = startPlaces(g, prev, saved);
  const isKnown = (id) => prev.has(id) || !!saved[id];
  const known = g.nodes.filter((x) => isKnown(x.id)).length;
  if (known === g.nodes.length && saved['#shape'] === hash(sig)) {
    layoutJob = 0;
    return apply(g, start);  // every dot has its place and no link changed: drawn at once, no layout at all
  }
  // a new brain settles from scratch; a known one keeps every known dot pinned and only places the new ones,
  // so a refresh never shifts the brain under his eyes
  const pin = known / g.nodes.length >= 0.8;
  const sim = g.nodes.map((x) => {
    const p = start.get(x.id);
    if (!p) return { id: x.id, deg: x.deg };
    const fixed = pin && isKnown(x.id) ? { fx: p[0], fy: p[1] } : {};
    return { id: x.id, deg: x.deg, x: p[0], y: p[1], ...fixed };
  });
  const simLinks = g.links.map((l) => ({ source: l.source, target: l.target }));
  settle(sim, simLinks, known === 0 ? 1 : pin ? 0.3 : 0.5, (at) => apply(g, at));
}
function hash(s) { let v = 5381; for (let i = 0; i < s.length; i++) v = ((v << 5) + v + s.charCodeAt(i)) | 0; return String(v >>> 0); }

function apply(g, at) {
  const visible = revealed && shown;
  const moves = [];
  const nodes = g.nodes.map((x) => {
    const o = byId.get(x.id);
    const to = at.get(x.id);
    if (o && visible) { Object.assign(o, x); moves.push([o, [o.x, o.y], to]); return o; }  // glides there
    const n = o ? Object.assign(o, x) : { ...x };
    n.x = to[0]; n.y = to[1];
    if (!o && visible) { n.b = 0; n.s = 0.2; }  // a dot new to this visit grows in where it lands
    return n;
  });
  byId = new Map(nodes.map((x) => [x.id, x]));
  if (hover && !byId.has(hover.id)) hover = null;
  if (picked && !byId.has(picked.id)) picked = null;
  const links = g.links.map((l, i) => ({ source: l.source, target: l.target, seed: (i * 397) % 1400 }));
  if (!fg) build();
  fg.graphData({ nodes, links });
  fg.nodeVisibility((x) => !hiddenGroups.has(x.group)).linkVisibility((l) => !hiddenGroups.has(l.source.group) && !hiddenGroups.has(l.target.group));
  const moved = moves.filter(([, a, b]) => Math.hypot(a[0] - b[0], a[1] - b[1]) > 0.5);
  if (moved.length) { tween = { start: performance.now(), moves: moved }; } else savePositions();
  wake();
  gbox.querySelector('.kn-graph-wait').textContent = '';
  reveal();
}

function build() {
  readTheme();
  fg = new ForceGraph(canvasBox)
    .backgroundColor('rgba(0,0,0,0)')
    .nodeId('id')
    .nodeLabel(() => '')
    .nodeRelSize(1)
    .nodeCanvasObjectMode(() => 'replace')
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint(hitArea)
    .linkCanvasObjectMode(() => 'replace')
    .linkCanvasObject(drawLink)
    .warmupTicks(0)
    .cooldownTicks(0)  // the layout is ours (settle): force-graph only draws, so nothing ever jumps on its own
    .onRenderFramePre((_ctx, scale) => step(scale))
    .onRenderFramePost((ctx, scale) => {
      const f = focusNode();
      if (f && !hiddenGroups.has(f.group)) label(f, ctx, scale, px(f), py(f), radius(f) * (f.s || 1), true);
    })
    .onNodeDragEnd(() => savePositions());
  fg.d3Force('charge', null).d3Force('center', null);  // no forces inside force-graph: a drag moves just that dot
  // the mouse's place feeds the lean; a wheel, a drag of the paper or a pinch means he set the view himself
  canvasBox.addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'mouse') return;
    const { node, at } = nodeAt(e, false);
    pointer = at;
    if (node !== hover) { hover = node; canvasBox.style.cursor = node ? 'pointer' : ''; relight(); }
    wake();
  });
  canvasBox.addEventListener('pointerleave', () => { pointer = null; if (hover) { hover = null; relight(); } wake(); });
  canvasBox.addEventListener('wheel', () => { userMoved = true; }, { passive: true });
  // taps and clicks are ours too, on the dots as drawn: the library's hit map repaints only every 0.8 s, and on a
  // busy phone it takes a tap for a pan. A press that ends within 8 px and 0.6 s is a tap.
  let down = null;
  canvasBox.addEventListener('pointerdown', (e) => { userMoved = true; down = e.isPrimary ? { x: e.clientX, y: e.clientY, t: performance.now() } : null; });
  canvasBox.addEventListener('pointerup', (e) => {
    const d = down;
    down = null;
    if (d && e.button === 0 && Math.hypot(e.clientX - d.x, e.clientY - d.y) <= 8 && performance.now() - d.t < 600) clickAt(e);
  });
  new ResizeObserver(() => { if (shown) { sizeCanvas(); reveal(); } }).observe(canvasBox);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', relight);
}

function nodeAt(e, touch) {  // the dot under a pointer: the nearest drawn one within its own size, or 5 px (a finger 14)
  const rect = canvasBox.getBoundingClientRect();
  const at = fg.screen2GraphCoords(e.clientX - rect.left, e.clientY - rect.top);
  const k = fg.zoom();
  let node = null, best = Infinity;
  for (const x of fg.graphData().nodes) {
    if (hiddenGroups.has(x.group)) continue;
    const reach = Math.max(radius(x) * (x.s || 1) * k, touch ? 14 : 5);
    const d = Math.hypot(px(x) - at.x, py(x) - at.y) * k - reach;
    if (d <= 0 && d < best) { node = x; best = d; }
  }
  return { node, at };
}

function clickAt(e) {
  const x = nodeAt(e, TOUCH).node;
  if (!x) { if (picked) { picked = null; relight(); } return; }
  if (!TOUCH || picked === x) return go(graphHref(x));  // a mouse opens at once; a finger lights it first, opens on the second tap
  picked = x;
  relight();
  flyTo(lit());
}

function flyTo(ids) {  // bring these dots into view, never closer than 2.5x: one dot alone stays a dot, not a disc
  const ns = fg.graphData().nodes.filter((x) => ids.has(x.id));
  if (!ns.length) return;
  userMoved = true;
  const xs = ns.map((x) => x.x), ys = ns.map((x) => x.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const k = Math.min(2.5, (fg.width() - 120) / (x1 - x0 + 1), (fg.height() - 160) / (y1 - y0 + 1));
  fg.centerAt((x0 + x1) / 2, (y0 + y1) / 2, 600);
  fg.zoom(Math.max(k, 0.2), 600);
}

let flyTimer = null;
function graphFind(text) {  // the search box on the graph: light every match, fly to the best one
  const w = fold(text.trim());
  matches = null;
  if (fg && w) {
    const all = fg.graphData().nodes.filter((x) => !hiddenGroups.has(x.group));
    const hits = all.filter((x) => fold(x.label).includes(w));
    matches = new Set(hits.map((x) => x.id));
    clearTimeout(flyTimer);
    if (hits.length) flyTimer = setTimeout(() => { if (matches) flyTo(matches); }, 300);
  }
  relight();
}

// ------------------------------------------------------------------ search

function overviewView() {
  const top = [h('p', { class: 'kn-hint muted' }, 'Red dots are notes the agents wrote, grey ones Claude chats round their project, lines the links between them; ',
    'calls hang off the people who spoke in them. Type to light a note up, Enter searches mail, messages, chats and calls too.')];
  if (!overview) return [...top, h('p', { class: 'muted kn-wait' }, 'Counting what the agents know…')];
  const by = Object.fromEntries((overview.sources || []).map((s) => [s.source, s]));
  const tile = (key, count, sub, href) => h('a', { class: 'kn-tile', href, style: `--c:${SRC[key].color}` },
    h('span', { class: 'k' }, dot(key), srcLabel(key)), h('span', { class: 'v' }, num(count)), h('span', { class: 's' }, sub));
  const tiles = [
    ...['claude-sessions', 'imessage', 'icloud-mail', 'hermes'].filter((s) => by[s]).map((s) =>
      tile(s, by[s].items, by[s].newest ? 'newest ' + ago(by[s].newest) : '', searchHash('', s) + '&browse=1')),
    tile('transcripts', overview.calls, 'recorded calls', '#/calls'),
    tile('brain', overview.notes, 'notes in the brain', '#/notes'),
  ];
  const errs = Object.entries(overview.errors || {});
  const recentNotes = notes ? (notes.notes || []).slice(0, 4) : [];
  const recentCalls = calls ? (calls.calls || []).slice(0, 3) : [];
  return [
    ...top,
    h('section', { class: 'kn-tiles' }, tiles),
    errs.length ? h('p', { class: 'error' }, errs.map(([k, e]) => `${k}: ${e}`).join(' · ')) : null,
    recentNotes.length ? h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Notes changed lately'), h('a', { class: 'more-link', href: '#/notes' }, 'All notes →')),
      h('div', { class: 'note-grid' }, recentNotes.map((x) => noteCard(x)))) : null,
    recentCalls.length ? h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Latest calls'), h('a', { class: 'more-link', href: '#/calls' }, 'All calls →')),
      h('div', { class: 'call-list' }, recentCalls.map((c) => callCard(c, true)))) : null,
  ];
}

function hitHref(x) {
  if (x.source === 'transcripts') return `#/call/${encodeURIComponent(x.thread)}?q=${encodeURIComponent(route().q)}`;
  return `#/t/${encodeURIComponent(x.thread)}?at=${encodeURIComponent(x.id)}&q=${encodeURIComponent(route().q)}`;
}

function hitRow(x, ws) {
  const title = x.title || (x.source === 'claude-sessions' ? (x.project ? `Chat in ${x.project}` : 'Claude chat') : null);
  return h('a', { class: 'kn-hit', href: hitHref(x) },
    h('div', { class: 'hit-meta' }, dot(x.source), h('span', { class: 'src' }, srcLabel(x.source)),
      h('span', { class: 'who' }, x.who), x.more ? h('span', { class: 'also' }, `+${x.more} more here`) : null, h('span', { class: 'when' }, when(x.ts))),
    title ? h('div', { class: 'hit-title' }, marked(title, ws)) : null,
    h('div', { class: 'hit-snip' }, marked(x.snippet, ws)));
}

function noteHit(x, ws) {
  return h('a', { class: 'kn-hit', href: '#/n/' + encodeURIComponent(x.path) + '?q=' + encodeURIComponent(route().q) },
    h('div', { class: 'hit-meta' }, dot('brain'), h('span', { class: 'src' }, SECTIONS[x.section] || 'Note'),
      x.topic ? h('span', { class: 'who' }, x.topic.replace(/-/g, ' ')) : null, h('span', { class: 'when' }, 'changed ' + when(x.changed))),
    h('div', { class: 'hit-title' }, marked(x.title, ws)),
    h('div', { class: 'hit-snip' }, marked(x.snippet, ws)));
}

function gbrainHit(x, ws) {
  const q = encodeURIComponent(route().q);  // a page from the corpus opens as its thread, a call as the call
  const href = !x.thread ? '#/g/' + encodeURIComponent(x.slug) : (x.type === 'call' ? '#/call/' : '#/t/') + encodeURIComponent(x.thread) + '?q=' + q;
  return h('a', { class: 'kn-hit', href },
    h('div', { class: 'hit-meta' }, dot('gbrain'), h('span', { class: 'src' }, 'gbrain'), h('span', { class: 'who' }, x.type || ''),
      h('span', { class: 'when' }, x.date || '')),
    h('div', { class: 'hit-title' }, marked(x.title, ws)),
    h('div', { class: 'hit-snip' }, marked(x.snippet, ws)));
}

function chips(r, counts) {
  const all = [['', 'All'], ...ORDER.map((k) => [k, srcLabel(k)])];
  return h('nav', { class: 'kn-chips', 'aria-label': 'Where to search' }, all.map(([k, label]) =>
    h('a', { class: 'kchip' + (r.source === k ? ' on' : ''), href: searchHash(r.q, k) }, k ? dot(k) : null, label,
      counts && counts[k] != null ? h('span', { class: 'count' }, String(counts[k])) : null)));
}

function resultsView(r) {
  const key = r.q + '|' + r.source;
  if (!results || results.key !== key) {
    runSearch(r);
    return [chips(r), h('p', { class: 'muted kn-wait' }, 'Searching mail, messages, chats, calls and notes…')];
  }
  const d = results.data, ws = words(r.q);
  if (d.error) return [chips(r), h('p', { class: 'error' }, d.error)];
  // one row per conversation: five lines of the same call are one hit that says 5 matches
  const byThread = new Map();
  for (const x of (d.corpus && d.corpus.items) || []) {
    const k = x.source + '|' + x.thread;
    if (byThread.has(k)) byThread.get(k).more++; else byThread.set(k, { ...x, more: 0 });
  }
  const corpus = [...byThread.values()], brain = (d.brain && d.brain.items) || [], gb = (d.gbrain && d.gbrain.items) || [];
  const counts = { brain: d.brain ? brain.length : null, gbrain: d.gbrain ? gb.length : null };
  if (!r.source) { counts[''] = corpus.length + brain.length + gb.length; for (const x of corpus) counts[x.source] = (counts[x.source] || 0) + 1; }
  const lane = (key, title, items, row, err, hint) => {
    if (!items.length && !err) return null;
    const open = openLanes.has(key + '|' + results.key), cap = open ? items.length : LANE;
    return h('section', { class: 'panel kn-lane' },
      h('div', { class: 'panel-head' }, h('h2', {}, title, items.length ? ` · ${items.length}` : '')),
      err ? h('p', { class: 'lane-err' }, err) : null,
      hint ? h('p', { class: 'muted lane-hint' }, hint) : null,
      h('div', { class: 'hits' }, items.slice(0, cap).map((x) => row(x, ws))),
      items.length > cap ? h('button', { class: 'show-more', onclick: () => { openLanes.add(key + '|' + results.key); render(); } }, `Show all ${items.length}`) : null);
  };
  const partial = corpus.some((x) => x.partial);
  const none = !corpus.length && !brain.length && !gb.length;
  return [
    chips(r, counts),
    none ? h('p', { class: 'kn-none' }, `Nothing found for “${r.q}”. Try one word, or the start of one with * (schůzk*).`) : null,
    lane('brain', 'In the brain notes', brain, noteHit, d.brain && d.brain.error),
    lane('corpus', 'In mail, messages, chats and calls', corpus, hitRow, d.corpus && d.corpus.error,
      partial ? 'No message has every word, so these have some of them.' : null),
    lane('gbrain', 'In gbrain', gb, gbrainHit, d.gbrain && d.gbrain.error),
  ];
}

let searchSeq = 0;
async function runSearch(r) {
  const seq = ++searchSeq, key = r.q + '|' + r.source;
  try {
    const data = await api('/api/knowledge/search?' + new URLSearchParams({ q: r.q, source: r.source }));
    if (seq !== searchSeq) return;
    results = { key, data };
  } catch (e) {
    if (seq !== searchSeq) return;
    results = { key, data: { error: e.message } };
  }
  render();
}

async function browseSource(r) {  // a tile: the newest items of one source, no words needed
  const key = '*|' + r.source;
  if (results && results.key === key) return;
  try { results = { key, data: await api('/api/knowledge/search?' + new URLSearchParams({ q: '*', source: r.source })) }; }
  catch (e) { results = { key, data: { error: e.message } }; }
  render();
}

// ------------------------------------------------------------------ a thread

const cache = new Map();  // fetched threads, calls, notes: back and forth stays instant
async function fetchOnce(path) {
  if (cache.has(path)) return cache.get(path);
  const p = api(path).catch((e) => ({ error: e.message }));
  cache.set(path, p);
  const v = await p;
  if (v.error) cache.delete(path);
  return v;
}

function back(label) {
  return h('a', { class: 'back', href: '#', onclick: (e) => { e.preventDefault(); history.length > 1 ? history.back() : go('#/'); } }, '← ' + (label || 'Back'));
}

function threadView(t, r) {
  const ws = words(r.q);
  const mail = t.source === 'icloud-mail';
  const head = h('div', { class: 'doc-head' }, back(),
    h('div', { class: 'hit-meta' }, dot(t.source), h('span', { class: 'src' }, srcLabel(t.source)),
      t.project ? h('span', { class: 'who' }, t.project) : null, h('span', { class: 'when' }, 'started ' + when(t.start, true))),
    h('h1', {}, marked(t.title, ws)),
    h('p', { class: 'muted' }, t.total > t.shown ? `${num(t.shown)} of ${num(t.total)} messages, around the one you opened.` : `${num(t.total)} message${t.total === 1 ? '' : 's'}.`,
      t.source === 'claude-sessions' ? [' ', h('a', { href: '/usage#/session/' + encodeURIComponent(t.id) }, 'This session in Usage →')] : null));
  const items = t.items.map((x) => {
    const cls = 'msg' + (x.me ? ' me' : '') + (x.id === r.at ? ' hit-here' : '') + (mail ? ' mail' : '') + (x.kind === 'session-agent' ? ' agent' : '');
    return h('article', { class: cls, id: 'i-' + x.id },
      h('div', { class: 'msg-meta' }, h('strong', {}, x.kind === 'session-agent' ? 'Helper agent' : x.who),
        mail && x.to ? h('span', {}, ' to ' + x.to) : null, h('span', { class: 'when' }, when(x.ts, true))),
      h('div', { class: 'msg-text' }, marked(x.text, ws)),
      x.cut ? h('p', { class: 'muted cut' }, 'Long message, cut here.') : null);
  });
  return [h('section', { class: 'doc' }, head, h('div', { class: 'thread' + (mail ? ' mails' : '') }, items))];
}

// ------------------------------------------------------------------ calls

function speakerColors(list) { const m = new Map(); list.forEach((w, i) => m.set(w, SPEAKER[i % SPEAKER.length])); return m; }
const shortName = (w) => (w.length > 34 ? w.slice(0, 32) + '…' : w);

function talkBar(speakers) {
  const total = speakers.reduce((s, x) => s + x.seconds, 0) || 1;
  const col = speakerColors(speakers.map((s) => s.who));
  return h('div', { class: 'talk' },
    h('div', { class: 'talk-bar', role: 'img', 'aria-label': 'Who talked how much' }, speakers.filter((s) => s.seconds > 0).map((s) =>
      h('span', { style: `width:${(s.seconds / total) * 100}%;background:${col.get(s.who)}`, title: `${s.who}: ${Math.round((s.seconds / total) * 100)}% of the talking` }))),
    h('div', { class: 'talk-legend' }, speakers.map((s) => h('span', { title: s.who }, h('i', { style: `background:${col.get(s.who)}` }),
      shortName(s.who), h('b', {}, ` ${Math.round((s.seconds / total) * 100)}%`)))));
}

function callCard(c, compact) {
  const isMe = (who) => window.BEACON.owner.names.includes(who);
  const others = c.speakers.map((s) => s.who).filter((w) => !isMe(w));
  return h('a', { class: 'call-card' + (compact ? ' compact' : ''), href: '#/call/' + encodeURIComponent(c.id) },
    h('div', { class: 'call-date' }, h('b', {}, new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { timeZone: TZ, day: 'numeric' }).format(new Date(c.start * 1000))),
      h('span', {}, new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { timeZone: TZ, month: 'short' }).format(new Date(c.start * 1000))),
      h('small', {}, `${c.minutes} min`)),
    h('div', { class: 'call-body' },
      h('div', { class: 'call-title' }, marked(c.title, words(filter))),
      h('div', { class: 'call-with muted' }, marked('with ' + (others.map(shortName).join(', ') || 'no one named'), words(filter))),
      compact ? null : talkBar(c.speakers),
      !compact && c.summary ? h('p', { class: 'call-sum' }, marked(c.summary, words(filter))) : null,
      !compact && (c.decisions || c.actions) ? h('div', { class: 'call-counts' },
        c.decisions ? h('span', {}, `${c.decisions} decision${c.decisions > 1 ? 's' : ''}`) : null,
        c.actions ? h('span', {}, `${c.actions} to-do${c.actions > 1 ? 's' : ''}`) : null) : null));
}

function callsView() {
  if (!calls) return [h('p', { class: 'muted kn-wait' }, 'Loading the calls…')];
  if (calls.error) return [h('p', { class: 'error' }, calls.error)];
  const ws = words(filter);
  const list = (calls.calls || []).filter((c) => !ws.length || ws.every((w) => fold([c.title, c.summary, ...c.speakers.map((s) => s.who)].join(' ')).includes(w)));
  const mins = list.reduce((s, c) => s + c.minutes, 0);
  return [
    h('p', { class: 'kn-sum' }, h('strong', {}, String(list.length)), ` call${list.length === 1 ? '' : 's'} · `, h('strong', {}, `${Math.round(mins / 6) / 10} h`),
      ' recorded', filter ? ` · matching “${filter}”` : ''),
    h('div', { class: 'call-list' }, list.slice(0, PAGE * more).map((c) => callCard(c))),
    list.length > PAGE * more ? h('button', { class: 'show-more', onclick: () => { more++; render(); } }, `Show ${Math.min(PAGE, list.length - PAGE * more)} more`) : null,
  ];
}

function callView(c, r) {
  const ws = words(r.q || filter);
  const col = speakerColors(c.speakers);
  const list = (title, xs) => xs.length ? h('section', { class: 'digest-part' }, h('h3', {}, title), h('ul', {}, xs.map((x) => h('li', {}, x)))) : null;
  let first = true;
  const turns = c.turns.map((t) => {
    const hit = ws.length && ws.some((w) => fold(t.text).includes(w));
    const el = h('div', { class: 'kn-turn' + (hit ? ' hit-turn' : '') + (window.BEACON.owner.names.includes(t.who) ? ' me' : ''), style: `--c:${col.get(t.who)}`, id: hit && first ? 'first-hit' : null },
      h('div', { class: 'turn-who' }, h('i', {}), h('strong', {}, shortName(t.who)), h('span', { class: 'when' }, mmss(t.at))),
      h('div', { class: 'turn-text' }, marked(t.text, ws)));
    if (hit) first = false;
    return el;
  });
  return [h('section', { class: 'doc' },
    h('div', { class: 'doc-head' }, back('Calls'),
      h('div', { class: 'hit-meta' }, dot('transcripts'), h('span', { class: 'src' }, 'Call'), h('span', { class: 'when' }, when(c.start, true) + ` · ${c.minutes} min`)),
      h('h1', {}, c.title),
      h('div', { class: 'talk-legend' }, c.speakers.map((w) => h('span', {}, h('i', { style: `background:${col.get(w)}` }), w)))),
    c.summary ? h('section', { class: 'digest' }, h('p', { class: 'call-sum big' }, c.summary),
      h('div', { class: 'digest-cols' }, list('Decided', c.decisions), list('To do', c.actions), list('Still open', c.questions))) : null,
    h('h2', { class: 'tr-head' }, 'Who said what', h('span', { class: 'muted' }, ` · ${num(c.turns.length)} turns`)),
    h('div', { class: 'transcript' }, turns))];
}

// ------------------------------------------------------------------ notes

function noteCard(x) {
  const ws = words(filter);
  return h('a', { class: 'note-card', href: '#/n/' + encodeURIComponent(x.path) },
    h('div', { class: 'note-meta' }, h('span', { class: 'sec sec-' + x.section }, SECTIONS[x.section] || x.section),
      x.topic ? h('span', { class: 'topic' }, x.topic.replace(/-/g, ' ')) : null, h('span', { class: 'when' }, when(x.changed))),
    h('div', { class: 'note-title' }, marked(x.title, ws)),
    x.blurb ? h('p', { class: 'note-blurb' }, marked(x.blurb, ws)) : null);
}

function notesView(r) {
  if (!notes) return [h('p', { class: 'muted kn-wait' }, 'Loading the notes…')];
  if (notes.error) return [h('p', { class: 'error' }, notes.error)];
  const all = notes.notes || [];
  const sec = r.id && SECTIONS[r.id] ? r.id : '';
  const ws = words(filter);
  const list = all.filter((x) => (!sec || x.section === sec) && (!ws.length || ws.every((w) => fold([x.title, x.blurb, x.topic, x.path].join(' ')).includes(w))));
  const count = (s) => all.filter((x) => x.section === s).length;
  return [
    h('nav', { class: 'kn-chips' }, [['', 'All', all.length], ...Object.entries(SECTIONS).map(([k, l]) => [k, l, count(k)])].map(([k, l, c]) =>
      h('a', { class: 'kchip' + (sec === k ? ' on' : ''), href: '#/notes' + (k ? '/' + k : '') }, l, h('span', { class: 'count' }, String(c))))),
    filter ? h('p', { class: 'kn-sum' }, h('strong', {}, String(list.length)), ` note${list.length === 1 ? '' : 's'} matching “${filter}”. Press Enter to search inside every note.`) : null,
    h('div', { class: 'note-grid' }, list.slice(0, PAGE * more).map(noteCard)),
    list.length > PAGE * more ? h('button', { class: 'show-more', onclick: () => { more++; render(); } }, `Show ${Math.min(PAGE, list.length - PAGE * more)} more of ${list.length}`) : null,
  ];
}

function docView(d, r, kind) {
  const body = h('article', { class: 'md', html: noteHtml(d.text, d.path) });
  const ws = words(r.q);
  if (ws.length) {  // mark the search words inside the rendered note, text nodes only
    const walk = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const nodes = []; while (walk.nextNode()) nodes.push(walk.currentNode);
    for (const t of nodes) { const parts = marked(t.nodeValue, ws); if (parts.length > 1) t.replaceWith(...parts); }
  }
  return [h('section', { class: 'doc' },
    h('div', { class: 'doc-head' }, back(kind === 'note' ? 'Notes' : 'Back'),
      h('div', { class: 'hit-meta' }, dot(kind === 'note' ? 'brain' : 'gbrain'),
        h('span', { class: 'src' }, kind === 'note' ? (SECTIONS[d.section] || 'Note') : 'gbrain' + (d.type ? ' · ' + d.type : '')),
        d.changed ? h('span', { class: 'when' }, 'changed ' + when(d.changed, true)) : d.date ? h('span', { class: 'when' }, String(d.date).slice(0, 10)) : null),
      h('h1', {}, d.title)),
    body)];
}

// ------------------------------------------------------------------ render

async function detail(r) {
  const path = r.view === 't' ? '/api/knowledge/thread?' + new URLSearchParams({ id: r.id, ...(r.at ? { at: r.at } : {}) })
    : r.view === 'call' ? '/api/knowledge/call?' + new URLSearchParams({ id: r.id })
      : r.view === 'n' ? '/api/knowledge/note?' + new URLSearchParams({ path: r.id })
        : '/api/knowledge/gbrain?' + new URLSearchParams({ slug: r.id });
  const d = await fetchOnce(path);
  if (route().view !== r.view || route().id !== r.id) return;  // he moved on meanwhile
  if (d.error) return $('view').replaceChildren(h('section', { class: 'doc' }, back(), h('p', { class: 'error' }, d.error)));
  $('view').replaceChildren(...(r.view === 't' ? threadView(d, r) : r.view === 'call' ? callView(d, r) : docView(d, r, r.view === 'n' ? 'note' : 'gbrain')).filter(Boolean));
  const target = r.view === 't' && r.at ? document.getElementById('i-' + r.at) : document.getElementById('first-hit') || document.querySelector('.md mark');
  (target || document.body).scrollIntoView({ block: target ? 'center' : 'start' });
}

let lastView = null;
function render() {
  const r = route();
  const tab = ['t', 'call', 'n', 'g'].includes(r.view) ? (r.view === 'call' ? 'calls' : r.view === 'n' ? 'notes' : 'search') : r.view;
  for (const a of document.querySelectorAll('#views a')) a.classList.toggle('on', a.dataset.view === tab);
  $('notes-count').textContent = notes && notes.notes ? String(notes.notes.length) : '';
  $('calls-count').textContent = calls && calls.calls ? String(calls.calls.length) : '';
  if (r.view !== lastView) { more = 1; lastView = r.view; }
  const q = $('q');
  if (tab === 'search' && r.view === 'search' && document.activeElement !== q) q.value = r.q;
  q.placeholder = tab === 'notes' ? 'Filter notes, Enter searches inside them' : tab === 'calls' ? 'Filter calls by name or topic' : r.q ? 'Search mail, messages, chats, calls, notes' : 'Find a note, Enter searches everything';
  showGraph(r.view === 'search' && !r.q && !r.source);  // the one graph: shown or hidden, never rebuilt
  if (['t', 'call', 'n', 'g'].includes(r.view)) {
    if (!$('view').querySelector('.doc') || $('view').dataset.key !== location.hash) $('view').replaceChildren(h('p', { class: 'muted kn-wait' }, 'Opening…'));
    $('view').dataset.key = location.hash;
    return detail(r);
  }
  $('view').dataset.key = '';
  let out;
  if (r.view === 'notes') out = notesView(r);
  else if (r.view === 'calls') out = callsView();
  else if (r.q) out = resultsView(r);
  else if (r.source && /browse=1/.test(location.hash)) {
    const key = '*|' + r.source;
    if (!results || results.key !== key) browseSource(r);
    const d = results && results.key === key ? results.data : null;
    const items = d && d.corpus ? d.corpus.items : [];
    out = [h('div', { class: 'doc-head' }, back(), h('h1', {}, dot(r.source), ' ', srcLabel(r.source), h('span', { class: 'muted' }, ' · newest first'))),
      d && d.error ? h('p', { class: 'error' }, d.error) : null,
      d ? h('section', { class: 'panel kn-lane' }, h('div', { class: 'hits' }, items.map((x) => hitRow(x, [])))) : h('p', { class: 'muted kn-wait' }, 'Loading…')];
  } else out = overviewView();
  $('view').replaceChildren(...out.flat().filter(Boolean));
}

async function load() {
  api('/api/knowledge/graph').then((g) => { snap.set('kn.graph', g); setGraph(g); }, (e) => { if (!graph) setGraph({ error: e.message }); });
  const [o, nt, c] = await Promise.all([
    api('/api/knowledge/overview').catch((e) => ({ error: e.message })),
    api('/api/knowledge/notes').catch((e) => ({ error: e.message })),
    api('/api/knowledge/calls').catch((e) => ({ error: e.message })),
  ]);
  const denied = [o, nt, c].find((x) => x.error && /Not allowed/.test(x.error));
  showError(denied ? new Error('Only you can read this page: open it as http://beacon on your own tailnet login.') : null);
  if (!o.error) { overview = o; snap.set('kn.overview', o); }
  if (!nt.error) { notes = nt; snap.set('kn.notes', nt); } else if (!notes) notes = nt;
  if (!c.error) { calls = c; snap.set('kn.calls', c); } else if (!calls) calls = c;
  const r = route();
  if (!['t', 'call', 'g'].includes(r.view)) render();  // a note may need the list to resolve its links: n re-renders
}

$('form').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = $('q').value.trim();
  const r = route();
  filter = '';
  if (!q) return go('#/');
  results = null;
  go(searchHash(q, r.view === 'search' ? r.source : r.view === 'notes' ? 'brain' : r.view === 'calls' ? 'transcripts' : ''));
  $('q').blur();
});
$('q').addEventListener('input', () => {
  const r = route();
  if (r.view === 'notes' || r.view === 'calls') { filter = $('q').value.trim(); more = 1; render(); }
  else if (r.view === 'search' && !r.q && !r.source) graphFind($('q').value);
});
document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== $('q')) { e.preventDefault(); $('q').focus(); }
});
window.addEventListener('hashchange', () => {
  const r = route();
  if (r.view !== 'notes' && r.view !== 'calls' && filter) { filter = ''; }
  if ((r.view === 'notes' || r.view === 'calls') && !filter) $('q').value = '';
  if (matches) { matches = null; }  // a search's lit dots don't follow him to another page
  render();
});

// phones: the Home row scrolls sideways, so bring the lit Knowledge tab into view
{ const on = document.querySelector('.top .tabs .on'); if (on) on.parentElement.scrollLeft = on.offsetLeft + on.offsetWidth - on.parentElement.clientWidth; }
render();  // the last data first, no blank page
setGraph(graph);
load();
