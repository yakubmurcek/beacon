// Machines: every tailnet box, how it reaches the hub, its health and its background jobs.
// Reads GET /api/machines (contract in hub/machines.py). Lighthouse's wall TV draws the same data.
// The map is Lighthouse's TailnetScene on paper: the hub in the middle, every box a spoke.
import { $, api, DAY, h, showError, snap } from './shared.js';

const HUB = window.BEACON.hub.machine;
// a job whose description is only its machine's name ("devbox: …") says nothing more than the card does
const MACHINE_PREFIX = new RegExp(`^(${window.BEACON.machines.join('|')})\\b(:|$)`);
const REFRESH_MS = 30000;
const BAD_JOB = new Set(['failed', 'overdue', 'gone']);
const STATE_LABEL = { ok: 'OK', warn: 'Attention', bad: 'Problem', asleep: 'Asleep', offline: 'Offline' };
const JOB_LABEL = { failed: 'Failed', overdue: 'Overdue', gone: 'Gone', running: 'Running', ok: 'OK', never: 'Never ran', unknown: 'Unknown' };
const KEY_WARN_SEC = 30 * DAY;

let data = snap.get('machines');
let names = snap.get('names');  // GET /api/names: every tailnet name, from scripts/tailnet_check.py
let query = '';

// ------------------------------------------------------------------ formatting

const GB = 1e9;
const gb = (b) => (b >= 100 * GB ? Math.round(b / GB) : (b / GB).toFixed(1)) + ' GB';
const pct = (used, total) => (total > 0 ? Math.round((used / total) * 100) : 0);

function span(sec) {  // 3 min, 5 h, 2 d
  sec = Math.abs(sec);
  if (sec < 90) return `${Math.max(1, Math.round(sec))} s`;
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  if (sec < 2 * DAY) return `${Math.round(sec / 3600)} h`;
  return `${Math.round(sec / DAY)} d`;
}
const now = () => (data && data.now ? data.now + (Date.now() / 1000 - data.fetchedAt) : Date.now() / 1000);
const since = (ts) => (ts ? span(now() - ts) + ' ago' : '');
const until = (ts) => (!ts ? '' : ts < now() ? since(ts) : 'in ' + span(ts - now()));

// Go's zero time (year 1) is what tailscale reports for a node that is online right now.
const lastSeen = (ts) => (ts && ts > 0 ? ts : null);

function disks(stats) {  // bind mounts of one pool report the same free space: show the pool once
  const seen = new Set();
  return (stats.disks || []).filter((d) => {
    if (seen.has(d.free)) return false;
    seen.add(d.free);
    return true;
  });
}

function badJobs(m) { return (m.jobs || []).filter((j) => j.own && BAD_JOB.has(j.status)); }  // only his jobs alarm

/** The one line under a box's name on the map. */
function detail(m) {
  if (m.problems && m.problems.length && m.online) return m.problems[0];
  if (!m.online) {
    const seen = lastSeen(m.tailscale && m.tailscale.last_seen) || m.seen_at;
    return (m.state === 'asleep' ? 'asleep' : 'offline') + (seen ? ' · ' + span(now() - seen) : '');
  }
  const s = m.stats;
  if (!s) return m.tailscale && m.tailscale.os ? m.tailscale.os : 'online';
  return `RAM ${pct(s.mem_total - s.mem_avail, s.mem_total)}%` + (s.temp_c != null ? ` · ${s.temp_c}°` : '');
}

function matches(...parts) {
  if (!query) return true;
  return parts.some((p) => String(p || '').toLowerCase().includes(query));
}

// ------------------------------------------------------------------ the map
// Built once and updated in place on every refresh, so its motion never restarts. Every movement is real data:
// a link flows only while its box is reporting, a fresh report sends one packet to the hub, a box that needs a
// look breathes in its colour, the ring around a dot is its RAM. Tap a box for its sheet; tap the hub to ask again.

const SVG = 'http://www.w3.org/2000/svg';
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) el.append(k.nodeType ? k : String(k));
  return el;
}
const CALM = matchMedia('(prefers-reduced-motion: reduce)');
const FRESH_SEC = 120;  // a collector reports every 10 s: two minutes of silence stops the flow

/** Order around the ring: boxes with a job (always-on first), then phones and the rest. */
function ringOrder(list) {
  const rank = (m) => (m.always_on ? 0 : m.stats || m.seen_at ? 1 : m.role ? 2 : 3);
  return list.filter((m) => m.machine !== HUB).sort((a, b) => rank(a) - rank(b) || a.machine.localeCompare(b.machine));
}

const ramPct = (m) => (m.stats && m.stats.mem_total ? pct(m.stats.mem_total - m.stats.mem_avail, m.stats.mem_total) : null);
const fresh = (m) => !!(m.online && m.seen_at && now() - m.seen_at < FRESH_SEC);

let map = null;       // { key, panel, svg, box, sheet, spots: Map(machine → spot) }
let selected = null;  // the machine whose sheet is open

function mapPanel(list) {
  const W = Math.max(320, Math.round($('main').clientWidth - (innerWidth < 760 ? 30 : 38)));
  const key = W + '|' + list.map((m) => m.machine).sort().join(',');
  const rebuilt = !map || map.key !== key;
  if (rebuilt) map = buildMap(list, W, key);
  updateMap(list);
  if (rebuilt && selected) requestAnimationFrame(() => select(selected));
  return map.panel;
}

function buildMap(list, W, key) {
  const first = !map;
  const phone = W < 600;
  const H = phone ? 400 : 460;
  const cx = W / 2, cy = H / 2;
  const rx = W / 2 - (phone ? 44 : 150), ry = H / 2 - (phone ? 52 : 60);
  const hubM = list.find((m) => m.machine === HUB);
  const ring = ringOrder(list);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: 'tmap' + (first && !CALM.matches ? ' intro' : ''),
    role: 'img', 'aria-label': `Tailnet map: every machine and how it reaches ${HUB}` });
  const defs = s('defs', {},
    s('radialGradient', { id: 'tm-core' }, s('stop', { offset: '0', class: 'core-a' }), s('stop', { offset: '1', class: 'core-b' })));
  // the orbit the boxes sit on, and a slower one inside it: structure without a grid
  const deco = s('g', { class: 'deco' },
    s('circle', { cx, cy, r: Math.min(rx, ry) * 1.02, class: 'halo-bg', fill: 'url(#tm-core)' }),
    s('ellipse', { cx, cy, rx, ry, class: 'orbit' }),
    s('ellipse', { cx, cy, rx: rx * 0.55, ry: ry * 0.55, class: 'orbit inner' }));
  const edges = s('g', { class: 'edges' }), packets = s('g', { class: 'packets' }), nodes = s('g', { class: 'nodes' });
  svg.append(defs, deco, edges, packets, nodes);
  const spots = new Map();

  const place = (m, x, y, cos, sin, i, isHub) => {
    const r = isHub ? (phone ? 16 : 21) : (phone ? 9 : 12);
    const g = s('g', { class: 'node' + (isHub ? ' hub' : ''), tabindex: 0, role: 'button', 'aria-label': m.machine,
      style: `--dx:${cx - x}px;--dy:${cy - y}px;--i:${i};transform-origin:${x}px ${y}px` });
    const arcR = r + (phone ? 4.5 : 6);
    const halo = s('circle', { class: 'halo', r: r + 10, cx: x, cy: y });
    const ping = s('circle', { class: 'ping', r: r + 6, cx: x, cy: y });
    const track = s('circle', { class: 'track', r: arcR, cx: x, cy: y });
    const arc = s('circle', { class: 'arc', r: arcR, cx: x, cy: y, pathLength: 100, transform: `rotate(-90 ${x} ${y})` });
    const dot = s('circle', { class: 'dot', r, cx: x, cy: y });
    const hit = s('circle', { class: 'hit', r: r + 22, cx: x, cy: y });
    g.append(hit, halo, ping, track, arc, dot);
    if (isHub) g.prepend(s('circle', { class: 'spin', r: r + 15, cx: x, cy: y }));
    // Captions point away from the hub, so a name never sits on its own spoke.
    const anchor = isHub || phone ? 'middle' : cos > 0.35 ? 'start' : cos < -0.35 ? 'end' : 'middle';
    const gap = arcR + (phone ? 5 : 8);
    const tx = x + (anchor === 'start' ? gap : anchor === 'end' ? -gap : 0);
    const ty = isHub ? y + arcR + (phone ? 20 : 26) : anchor === 'middle' ? (sin < 0 ? y - gap - (phone ? 16 : 20) : y + gap + (phone ? 10 : 14)) : y - 2;
    const name = s('text', { x: tx, y: ty, 'text-anchor': anchor, class: 'name' }, phone && m.machine.length > 11 ? m.machine.slice(0, 10) + '…' : m.machine);
    const det = s('text', { x: tx, y: ty + (phone ? 14 : 17), 'text-anchor': anchor, class: 'detail' });
    g.append(name, det);
    const pick = () => select(selected === m.machine ? null : m.machine);
    g.addEventListener('click', (e) => { e.stopPropagation(); pick(); });
    g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
    g.addEventListener('pointerenter', () => { svg.dataset.hot = m.machine; mark(); });
    g.addEventListener('pointerleave', () => { delete svg.dataset.hot; mark(); });
    nodes.append(g);
    return { g, x, y, r, arcR, det, isHub, cut: phone ? 15 : anchor === 'middle' ? 34 : 20, seen: null, state: null };
  };

  ring.forEach((m, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / ring.length + (ring.length % 2 ? 0 : Math.PI / ring.length);
    const cos = Math.cos(a), sin = Math.sin(a);
    const x = cx + rx * cos, y = cy + ry * sin;
    // drawn from the box to the hub: dashes flow inward, the way reports travel
    const line = { x1: x, y1: y, x2: cx, y2: cy };
    const edge = s('line', { ...line, class: 'edge', pathLength: 100, style: `--i:${i}` });
    const flow = s('line', { ...line, class: 'flow' });
    const label = phone ? null : s('text', { x: cx + (x - cx) * 0.55, y: cy + (y - cy) * 0.55 - 6, 'text-anchor': 'middle', class: 'edge-label' });
    edges.append(edge, flow, ...(label ? [label] : []));  // append(null) would draw the word "null"
    const spot = place(m, x, y, cos, sin, i, false);
    Object.assign(spot, { edge, flow, label, len: Math.hypot(x - cx, y - cy) });
    spots.set(m.machine, spot);
  });
  if (hubM) spots.set(HUB, place(hubM, cx, cy, 0, 0, 0, true));

  svg.addEventListener('click', () => select(null));
  const box = h('div', { class: 'map-box' }, svg);
  const sheet = h('aside', { class: 'map-sheet', hidden: true, 'aria-live': 'polite' });
  box.append(sheet);
  const sum = h('span', { class: 'sum' });
  const pulse = h('button', { class: 'map-refresh', type: 'button', title: 'Ask every box again', onclick: () => askAll() },
    h('span', { class: 'beat' }), h('span', { class: 'when' }));
  const panel = h('section', { class: 'panel map-panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Tailnet'), sum, pulse), box);
  if (first && !CALM.matches) setTimeout(() => svg.classList.remove('intro'), 2600);
  return { key, panel, svg, box, sheet, sum, pulse, packets, spots, W, H, cx, cy, phone, firstData: true };
}

function updateMap(list) {
  const { spots } = map;
  const hubM = list.find((m) => m.machine === HUB);
  const online = list.filter((m) => m.online).length;
  const needs = list.filter((m) => m.state === 'bad' || m.state === 'warn').length;
  map.sum.replaceChildren(h('strong', {}, `${online} of ${list.length}`), ' online',
    ...(needs ? [' · ', h('strong', { class: 'bad-t' }, `${needs} need${needs === 1 ? 's' : ''} a look`)] : [' · all fine']));
  map.pulse.querySelector('.when').textContent = data && data.fetchedAt ? 'live' : '';
  let delay = 0;
  for (const m of list) {
    const sp = spots.get(m.machine);
    if (!sp) continue;
    const ts = m.tailscale || {};
    const live = m.online && ts.online !== false && (!hubM || hubM.online);
    sp.g.setAttribute('class', `node st-${m.state}${sp.isHub ? ' hub' : ''}${m.online ? ' up' : ''}${fresh(m) ? ' fresh' : ''}`);
    const ram = ramPct(m);
    sp.g.style.setProperty('--ram', ram == null ? 0 : ram);
    sp.g.classList.toggle('has-arc', ram != null);
    sp.g.classList.toggle('arc-warn', ram != null && ram >= 80);
    sp.g.classList.toggle('arc-bad', ram != null && ram >= 90);
    const line2 = detail(m);
    sp.det.textContent = line2.length > sp.cut ? line2.slice(0, sp.cut - 1) + '…' : line2;
    if (sp.edge) {
      const cls = `${live ? 'live' : 'down'} ${ts.direct ? 'direct' : 'relay'} st-${m.state}`;
      sp.edge.setAttribute('class', 'edge ' + cls);
      sp.flow.setAttribute('class', `flow ${ts.direct ? 'direct' : 'relay'}${live && fresh(m) ? ' on' : ''}`);
      if (sp.label) sp.label.textContent = live ? (ts.direct ? 'direct' : ts.relay ? `relay ${ts.relay}` : 'relay') : '';
    }
    // a state that changed since the last look flashes once; a new report travels the spoke
    if (sp.state && sp.state !== m.state) flash(sp);
    if (!map.firstData && m.seen_at && sp.seen && m.seen_at > sp.seen && fresh(m) && !sp.isHub) {
      setTimeout(() => packet(sp, m.state), delay);
      delay += 140 + Math.random() * 220;
    }
    sp.state = m.state;
    sp.seen = m.seen_at || sp.seen;
  }
  map.firstData = !data;
  map.list = list;
  mark();
  if (selected) fillSheet(list.find((m) => m.machine === selected));
}

function mark() {
  const { svg, spots } = map;
  const focus = selected || svg.dataset.hot || null;
  svg.classList.toggle('focusing', !!focus);
  for (const [name, sp] of spots) {
    sp.g.classList.toggle('lit', name === focus || (focus === HUB));
    sp.g.classList.toggle('sel', name === selected);
    if (sp.edge) { sp.edge.classList.toggle('lit', name === focus || focus === HUB); sp.flow.classList.toggle('lit', name === focus || focus === HUB); }
  }
}

function flash(sp) {
  if (CALM.matches) return;
  const ring = s('circle', { cx: sp.x, cy: sp.y, r: sp.arcR, class: 'shock' });
  sp.g.append(ring);
  setTimeout(() => ring.remove(), 1400);
}

/** One report travelling from its box to the hub; the hub blinks when it lands. */
function packet(sp, state) {
  if (CALM.matches || document.hidden || !map) return;
  const hub = map.spots.get(HUB);
  const dot = s('circle', { cx: sp.x, cy: sp.y, r: map.phone ? 3 : 4, class: `pkt st-${state}` });
  map.packets.append(dot);
  const dur = 700 + sp.len * 1.6 * (sp.flow.classList.contains('relay') ? 1.6 : 1);
  const anim = dot.animate([{ transform: 'translate(0,0)', opacity: 0 }, { opacity: 1, offset: 0.12 },
    { transform: `translate(${map.cx - sp.x}px,${map.cy - sp.y}px)`, opacity: 1 }], { duration: dur, easing: 'cubic-bezier(.45,0,.25,1)' });
  anim.onfinish = () => { dot.remove(); if (hub) blip(hub); };
}

function blip(sp) {
  const ring = s('circle', { cx: sp.x, cy: sp.y, r: sp.r, class: 'blip' });
  sp.g.append(ring);
  setTimeout(() => ring.remove(), 900);
}

/** Tap the beat (or the hub's own sheet button): a wave goes out to every box and the answers ride back in. */
function askAll() {
  if (!map) return;
  if (!CALM.matches) {
    const wave = s('circle', { cx: map.cx, cy: map.cy, r: Math.hypot(map.W, map.H) / 2, class: 'wave', style: `transform-origin:${map.cx}px ${map.cy}px` });
    map.packets.append(wave);
    wave.animate([{ transform: 'scale(.04)', opacity: 0.9 }, { transform: 'scale(1)', opacity: 0 }], { duration: 1300, easing: 'cubic-bezier(.2,.8,.2,1)' })
      .onfinish = () => wave.remove();
    for (const sp of map.spots.values()) if (!sp.isHub && sp.g.classList.contains('up')) setTimeout(() => blip(sp), 250 + sp.len * 1.4);
  }
  map.pulse.classList.add('busy');
  load().finally(() => setTimeout(() => map && map.pulse.classList.remove('busy'), 400));
}

function select(name) {
  selected = name;
  if (!map) return;
  mark();
  const m = name && map.list ? map.list.find((x) => x.machine === name) : null;
  if (!m) { map.sheet.hidden = true; map.sheet.classList.remove('open'); return; }
  fillSheet(m);
  // the sheet sits on the far side from the box (under the map on a phone), so the box stays in view
  const sp = map.spots.get(name);
  const side = map.phone ? 'below' : sp.x > map.W / 2 ? 'left' : 'right';
  map.sheet.dataset.side = side;
  map.sheet.hidden = false;
  requestAnimationFrame(() => {
    map.sheet.classList.add('open');
    if (side === 'below') map.sheet.scrollIntoView({ block: 'nearest', behavior: CALM.matches ? 'auto' : 'smooth' });
  });
}

function fillSheet(m) {
  if (!m) return select(null);
  const st = m.stats;
  const bad = badJobs(m), jobs = m.jobs || [];
  const running = jobs.filter((j) => j.status === 'running').length;
  map.sheet.className = `map-sheet st-${m.state}` + (map.sheet.classList.contains('open') ? ' open' : '');
  map.sheet.replaceChildren(
    h('header', {}, h('span', { class: 'dot' }), h('h3', {}, m.machine), pill(m.state),
      h('button', { class: 'x', type: 'button', 'aria-label': 'Close', onclick: (e) => { e.stopPropagation(); select(null); } }, '×')),
    m.role ? h('p', { class: 'role' }, m.role) : null,
    m.problems && m.problems.length ? h('ul', { class: 'problems' }, m.problems.slice(0, 3).map((p) => h('li', {}, p))) : null,
    st ? h('div', { class: 'meters' }, meter('RAM', st.mem_total - st.mem_avail, st.mem_total),
      ...disks(st).slice(0, 2).map((d) => meter(`Disk ${d.mount}`, d.size - d.free, d.size, `${gb(d.free)} free`))) : null,
    st ? h('p', { class: 'facts' }, [st.cpu_pct != null ? `CPU ${st.cpu_pct}%` : null, st.temp_c != null ? `${st.temp_c} °C` : null,
      st.uptime_s ? `up ${span(st.uptime_s)}` : null, m.seen_at ? `reported ${since(m.seen_at)}` : null].filter(Boolean).join(' · ')) : null,
    connection(m),
    jobs.length ? h('div', { class: 'sheet-jobs' },
      h('p', {}, h('strong', {}, `${jobs.length} jobs`), bad.length ? h('span', { class: 'bad-t' }, ` · ${bad.length} failing`) : h('span', { class: 'good-t' }, ' · all fine'),
        running ? ` · ${running} running now` : ''),
      bad.length ? h('ul', {}, bad.slice(0, 3).map((j) => h('li', {}, j.name))) : null) : null,
    h('a', { class: 'sheet-more', href: `#/m/${encodeURIComponent(m.machine)}`, onclick: (e) => e.stopPropagation() },
      m.stats || m.seen_at || (m.always_on && m.role) ? 'Whole card ↓' : 'In the device list ↓'));
}

// ------------------------------------------------------------------ cards

function pill(state, text) { return h('span', { class: `pill st-${state}` }, text || STATE_LABEL[state] || state); }

function meter(label, used, total, extra) {
  const p = pct(used, total);
  const tone = p >= 90 ? 'bad' : p >= 80 ? 'warn' : 'ok';
  return h('div', { class: 'meter' },
    h('div', { class: 'meter-top' }, h('span', { class: 'meter-label' }, label), h('span', { class: 'meter-val' }, `${p}%`)),
    h('div', { class: 'bar' }, h('i', { class: `fill t-${tone}`, style: `width:${p}%` })),
    h('div', { class: 'meter-sub' }, extra || `${gb(used)} of ${gb(total)}`));
}

function connection(m) {
  const ts = m.tailscale;
  if (!ts) return h('p', { class: 'conn muted' }, 'Not on the tailnet list');
  const bits = [];
  if (ts.online) bits.push(h('strong', { class: 'on' }, 'Online'));
  else bits.push(h('strong', { class: 'off' }, 'Offline'), ` · last seen ${since(lastSeen(ts.last_seen)) || 'never'}`);
  if (m.machine !== HUB && ts.online) bits.push(' · ', ts.direct ? `direct to ${HUB}` : `through relay ${ts.relay || ''}`.trim());
  if (ts.ip) bits.push(' · ', h('code', {}, ts.ip));
  if (ts.key_expiry) {
    const left = ts.key_expiry - now();
    if (left < 0) bits.push(' · ', h('span', { class: 'warn-t' }, `key expired ${span(left)} ago`));
    else if (left < KEY_WARN_SEC) bits.push(' · ', h('span', { class: 'warn-t' }, `key expires in ${span(left)}`));
  }
  return h('p', { class: 'conn' }, h('span', { class: 'k' }, 'Tailscale '), ...bits);
}

function versions(m) {
  const v = m.versions || {};
  const rows = [['OS', v.os], ['Claude', v.claude && v.claude.replace(' (Claude Code)', '')], ['Codex', v.codex && v.codex.replace('codex-cli ', '')],
    ['Tailscale', v.tailscale], ['Python', v.python], ['Collector', v.collector]].filter(([, x]) => x);
  if (!rows.length) return null;
  return h('dl', { class: 'versions' }, rows.flatMap(([k, x]) => [h('dt', {}, k), h('dd', {}, x)]));
}

// ---- jobs, in plain words: grouped by what they do, one row per job however many boxes or folders run it

const JOB_ORDER = { failed: 0, overdue: 1, gone: 2, running: 3, ok: 4, never: 5, unknown: 6 };
const COMMAND = /^["']|\\|\.exe\b|^(conhost|powershell|pwsh|\/usr|\/opt|\/bin)/i;  // a Windows task's "description" is often its command line
const SCHEDULE = /^(every (\d+) s|always on|calendar|on demand)$/;  // a launchd agent's "description" is its schedule
/** @type {[string, string, (j: any, text: string) => boolean][]} */
const PURPOSE = [  // first match wins; checked against the live job list so "Other" stays small
  ['always', 'Apps and servers', (j) => j.kind === 'daemon' || j.desc === 'always on' || (j.kind === 'schtask' && j.status === 'running' && !j.next_run)],
  ['chore', 'Cleanup', (j) => /sweep|reaper|archiv|clean|follow-main|organiz|brake|guard/i.test(j.name)],
  ['watch', 'Watchers and alarms', (j, t) => /\b(watch|alarm|alert|check|checks|monitor|health)\b|telegram/i.test(t)],
  ['ai', 'AI agents at work', (j) => j.ai && j.ai.tools && j.ai.tools.length],
  ['sync', 'Sync and backups', (j, t) => /sync|backup|snapshot|push|pull|zaloha|copy|corpus|ingest|collect|update/i.test(t)],
  ['other', 'Data and helpers', () => true],
];
const PURPOSE_HINT = { always: 'always on', chore: 'tidy up after agents', watch: 'tell you when it breaks',
  ai: 'call a model', sync: 'keep boxes in step', other: 'numbers and small helpers' };

const isBad = (j) => BAD_JOB.has(j.status);
const jobKey = (name) => name.replace(/^com\.(yakub|lightdrop)\./, '').replace(/@.*/, '').replace(/^claude remote control\b.*/i, 'claude-remote-control')
  .toLowerCase().replace(/[\s_.]+/g, '-');
function folder(name) {  // claude-remote-control@beacon -> beacon; "Claude remote control snow" -> snow
  const at = name.match(/@(.+)$/) || name.match(/^claude remote control\s+(.+)$/i);
  return at ? at[1] : null;
}
const upper = (t) => t.charAt(0).toUpperCase() + t.slice(1);
function title(j) {
  const d = (j.desc || '').trim();
  if (!d || COMMAND.test(d) || SCHEDULE.test(d)) {
    return upper(j.name.replace(/^com\.(yakub|lightdrop|google|adobe)\./, '').replace(/@.*/, '').replace(/[-_.]+/g, ' ').trim());
  }
  const lead = d.match(/^([^:]{2,40}):\s+(.+)$/);  // "tasks-sweep: close open tasks…": the unit's own name says nothing
  if (lead && jobKey(lead[1]) === jobKey(j.name)) return upper(lead[2]);
  return d;
}
function cadence(j) {
  if (j.kind === 'daemon' || j.desc === 'always on') return 'always on';
  const every = (j.desc || '').match(SCHEDULE);
  if (every && every[2]) return often(+every[2]);
  if (j.next_run && j.last_run && j.next_run > j.last_run) return often(j.next_run - j.last_run);
  return '';
}
function often(sec) {  // timers drift a little: 3590 s reads "hourly", not "every 60 min"
  if (Math.abs(sec - DAY) < 1800) return 'daily';
  if (Math.abs(sec - 3600) < 120) return 'hourly';
  return 'every ' + (sec >= 3300 && sec < 2 * DAY ? `${Math.round(sec / 3600)} h` : span(sec));
}
const purpose = (j) => PURPOSE.find(([, , test]) => test(j, `${j.name} ${j.desc || ''}`))[0];

/** One row per job key: the same job on four boxes, or the same server for seven folders. */
function merge(pairs) {
  const by = new Map();
  for (const p of pairs) {
    const k = jobKey(p.j.name);
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(p);
  }
  return [...by.values()].map((ps) => {
    // the clearest description wins: a real sentence over a command line or a schedule
    const best = ps.find(({ j }) => j.desc && !COMMAND.test(j.desc) && !SCHEDULE.test(j.desc) && !folder(j.name)) ||
      ps.find(({ j }) => j.desc && !COMMAND.test(j.desc) && !SCHEDULE.test(j.desc)) || ps[0];
    const worst = ps.reduce((a, b) => (JOB_ORDER[b.j.status] < JOB_ORDER[a.j.status] ? b : a));
    return { ps, j: best.j, status: worst.j.status, title: title(best.j),
      machines: [...new Set(ps.map((p) => p.m))], folders: [...new Set(ps.map((p) => folder(p.j.name)).filter(Boolean))],
      last: Math.max(...ps.map((p) => p.j.last_run || 0)) || null };
  }).sort((a, b) => JOB_ORDER[a.status] - JOB_ORDER[b.status] || a.title.localeCompare(b.title));
}

function jobRow(item, showMachines) {
  const { j, status } = item;
  const bad = BAD_JOB.has(status);
  const when = bad ? (status === 'failed' && item.ps[0].j.bad_since ? `failing for ${span(now() - item.ps[0].j.bad_since)}` : JOB_LABEL[status])
    : status === 'running' ? 'running now' : cadence(j) || (item.last ? `ran ${since(item.last)}` : JOB_LABEL[status]);
  const sub = [];
  if (item.folders.length > 1) sub.push(`${item.folders.length} folders: ${item.folders.join(', ')}`);
  else if (item.ps.length > item.machines.length) sub.push(`${item.ps.length} copies`);
  const names = [...new Set(item.ps.map((p) => p.j.name))];
  return h('li', { class: `job js-${status}`, title: `${names.join(', ')}${item.last ? ` · last ran ${since(item.last)}` : ''}` },
    h('div', { class: 'job-top' },
      h('span', { class: `jdot ${bad ? 'bad' : status}` }),
      h('span', { class: 'job-name' }, item.title),
      h('span', { class: `job-when${bad ? ' bad-t' : ''}` }, when)),
    showMachines || sub.length ? h('div', { class: 'job-sub' },
      showMachines ? item.machines.map((m) => h('a', { class: 'job-m', href: `#/m/${encodeURIComponent(m)}` }, m)) : null,
      sub.length ? h('span', {}, sub.join(' · ')) : null) : null,
    bad && item.ps.find((p) => p.j.why) ? h('div', { class: 'job-why' }, item.ps.find((p) => p.j.why).j.why) : null);
}

function fold(id, summary, items, showMachines, openByDefault) {
  const open = query || (openMore.has(id) ? true : openByDefault && !openMore.has('-' + id));
  return h('details', { class: 'jfold', open: open ? true : null,
    ontoggle: (e) => { openMore.delete(id); openMore.delete('-' + id); openMore.add((e.target.open ? '' : '-') + id); } },
    h('summary', {}, summary),
    h('ul', { class: 'job-list' }, items.map((it) => jobRow(it, showMachines))));
}

/** The jobs of some machines: what needs a look first, then his jobs by purpose, then the system's own, folded. */
function jobGroups(pairs, id, showMachines) {
  const shown = pairs.filter(({ j, m }) => matches(m, j.name, j.desc, j.why, title(j)));
  const own = shown.filter((p) => p.j.own), builtin = shown.filter((p) => !p.j.own);
  const broken = merge(own.filter((p) => isBad(p.j)));
  const fine = merge(own.filter((p) => !isBad(p.j)));
  const out = [];
  if (broken.length) out.push(h('div', { class: 'jneeds' }, h('h3', {}, `Needs a look · ${broken.length}`),
    h('ul', { class: 'job-list' }, broken.map((it) => jobRow(it, showMachines)))));
  for (const [key, label] of PURPOSE) {
    const items = fine.filter((it) => purpose(it.j) === key);
    if (!items.length) continue;
    const running = items.filter((it) => it.status === 'running').length;
    out.push(fold(`${id}:${key}`, [h('strong', {}, label), h('span', { class: 'jcount' }, String(items.length)),
      h('span', { class: 'jhint' }, running ? `${running} running now` : PURPOSE_HINT[key])], items, showMachines, false));
  }
  if (builtin.length) {
    const items = merge(builtin);
    const stopped = items.filter((it) => BAD_JOB.has(it.status)).length;
    out.push(fold(`${id}:builtin`, [h('strong', {}, 'Built into the system'), h('span', { class: 'jcount' }, String(items.length)),
      h('span', { class: 'jhint' }, stopped ? `${stopped} stopped · not yours, no alarm` : 'updates, disk checks')],
    items, showMachines, false));
  }
  return out;
}

function jobsBlock(m) {
  const pairs = (m.jobs || []).map((j) => ({ j, m: m.machine }));
  if (!pairs.length) return null;
  const own = merge(pairs.filter((p) => p.j.own));
  const broken = own.filter((it) => BAD_JOB.has(it.status)).length;
  const groups = jobGroups(pairs, m.machine, false);
  if (!groups.length) return null;
  return h('div', { class: 'jobs' },
    h('h3', {}, `${own.length} jobs of yours`, broken ? h('span', { class: 'bad-t' }, ` · ${broken} need${broken === 1 ? 's' : ''} a look`) : h('span', { class: 'good-t' }, ' · all fine')),
    groups);
}

function card(m) {
  const st = m.stats;
  const reported = m.seen_at ? `reported ${since(m.seen_at)}` : 'no collector';
  return h('article', { class: `mcard st-${m.state}`, id: `m-${m.machine}` },
    h('header', { class: 'mcard-head' },
      h('span', { class: 'dot' }),
      h('h2', {}, m.machine), pill(m.state),
      h('span', { class: 'seen' }, reported)),
    m.role ? h('p', { class: 'role' }, m.role) : null,
    m.problems && m.problems.length ? h('ul', { class: 'problems' }, m.problems.map((p) => h('li', {}, p))) : null,
    st ? h('div', { class: 'meters' },
      meter('RAM', st.mem_total - st.mem_avail, st.mem_total),
      ...disks(st).map((d) => meter(`Disk ${d.mount}`, d.size - d.free, d.size, `${gb(d.free)} free of ${gb(d.size)}`))) : null,
    st ? h('p', { class: 'facts' }, [st.cpu_pct != null ? `CPU ${st.cpu_pct}%` : null, st.load ? `load ${st.load[0]}` : null,
      st.cpus ? `${st.cpus} cores` : null, st.temp_c != null ? `${st.temp_c} °C` : null, st.uptime_s ? `up ${span(st.uptime_s)}` : null]
      .filter(Boolean).join(' · ')) : null,
    connection(m),
    versions(m),
    jobsBlock(m));
}

function deviceRow(m) {
  const ts = m.tailscale || {};
  const seen = ts.online ? 'online' : `last seen ${since(lastSeen(ts.last_seen)) || 'never'}`;
  const how = ts.online ? (ts.direct ? ' · direct' : ts.relay ? ` · relay ${ts.relay}` : '') : '';
  let key = '';
  if (ts.key_expiry && ts.key_expiry - now() < KEY_WARN_SEC) key = ts.key_expiry < now() ? ` · key expired ${since(ts.key_expiry)}` : ` · key expires ${until(ts.key_expiry)}`;
  return h('li', { class: `device st-${m.state}`, id: `m-${m.machine}` },
    h('span', { class: 'dot' }), h('strong', {}, m.machine),
    h('span', { class: 'muted' }, ` ${m.role || ts.os || ''}`),
    h('span', { class: 'dev-seen' }, seen + how + key));
}

// ------------------------------------------------------------------ Claude sign-ins (hub/logins.py)

let logins = snap.get('logins');  // GET /api/logins: each box's Claude sign-in and a sign-in going on
const codes = {};  // box -> the code he is typing, kept across redraws
const FLOW_ON = new Set(['starting', 'waiting', 'checking']);
const LOGIN_ST = { out: 'bad', soon: 'warn', ok: 'ok', unknown: 'offline' };
let autoLogin = null;  // the box a notice's link (#/login/<box>) already started

function daysLeft(ts) {
  const d = Math.floor((ts - now()) / DAY);
  if (d >= 3) return `${d} days left`;
  return `ends ${new Date(ts * 1000).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}`;
}

function loginText(b) {
  const f = b.flow;
  if (f?.state === 'done') return `✅ Signed in · ${daysLeft(f.ends_at)}`;
  if (b.state === 'out') return 'signed out';
  return b.ends_at ? daysLeft(b.ends_at) : 'not checked yet';
}

async function startLogin(box) {
  try {
    await api(`/api/logins/${encodeURIComponent(box)}/start`, {});
  } catch (e) { showError(e); }
  pollLogins();
}

async function sendCode(box) {
  try {
    await api(`/api/logins/${encodeURIComponent(box)}/code`, { code: codes[box] || '' });
    delete codes[box];
  } catch (e) { showError(e); }
  pollLogins();
}

async function pollLogins() {
  try {
    logins = await api('/api/logins');
    snap.set('logins', logins);
  } catch (e) { showError(e); return; }
  render();
  // the page asks again while the box works (getting the link, checking the code), not while he copies the code
  if ((logins.boxes || []).some((b) => ['starting', 'checking'].includes(b.flow?.state))) setTimeout(pollLogins, 1500);
}

function flowBlock(b) {
  const f = b.flow;
  if (!f) return null;
  if (f.state === 'starting') return h('p', { class: 'login-step' }, 'Getting the sign-in link…');
  if (f.state === 'checking') return h('p', { class: 'login-step' }, 'Signing in…');
  if (f.state === 'failed') return h('p', { class: 'login-step bad-t' }, `Didn't work: ${f.error || 'unknown'}`);
  if (f.state !== 'waiting') return null;
  if (!f.url) return h('p', { class: 'login-step' }, 'Open this page on your own login to see the link.');
  const input = /** @type {HTMLInputElement} */ (h('input', { class: 'login-code', type: 'text', autocomplete: 'off', spellcheck: 'false',
    placeholder: 'Paste the code', value: codes[b.machine] || '' }));
  input.addEventListener('input', () => { codes[b.machine] = input.value.trim(); });
  return h('ol', { class: 'login-steps' },
    h('li', {}, h('a', { href: f.url, target: '_blank', rel: 'noopener' }, 'Open the sign-in page ↗')),
    h('li', {}, 'Approve, then copy the code it shows'),
    h('li', {}, h('form', { class: 'login-form', onsubmit: (e) => { e.preventDefault(); sendCode(b.machine); } },
      input, h('button', { type: 'submit', class: 'primary' }, 'Sign in'))));
}

function loginRow(b) {
  const busy = FLOW_ON.has(b.flow?.state);
  const st = b.flow?.state === 'done' ? 'ok' : LOGIN_ST[b.state] || 'offline';
  return h('li', { class: `login st-${st}`, id: `login-${b.machine}` },
    h('div', { class: 'login-head' }, h('span', { class: 'dot' }), h('strong', {}, b.machine),
      h('span', { class: 'muted' }, loginText(b)),
      busy ? null : h('button', { type: 'button', class: st === 'ok' ? 'login-go' : 'login-go primary', onclick: () => startLogin(b.machine) }, 'Sign in')),
    flowBlock(b));
}

function openLogin(box) {  // a notice's link (#/login/<box>): straight to that box's sign-in, started once
  document.getElementById(`login-${box}`)?.scrollIntoView({ block: 'center' });
  const b = (logins?.boxes || []).find((x) => x.machine === box);
  if (!b || autoLogin === box || FLOW_ON.has(b.flow?.state)) return;
  autoLogin = box;
  startLogin(box);
}

function showLogins(view) {  // on top of the overview, above the map: a sign-in is the one thing here he acts on
  $('logins-slot').replaceChildren(...(view === 'map' ? [loginsPanel()].filter(Boolean) : []));
}

function loginsPanel() {
  const boxes = logins?.boxes || [];
  if (!boxes.length) return null;
  return h('section', { class: 'panel logins', id: 'logins' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Claude sign-ins')),
    h('ul', {}, boxes.map(loginRow)));
}

// ------------------------------------------------------------------ views

function overview(list) {
  const full = list.filter((m) => m.stats || m.seen_at || (m.always_on && m.role));
  const devices = list.filter((m) => !full.includes(m));
  const shown = full.filter((m) => matches(m.machine, m.role, ...(m.problems || []), ...(m.jobs || []).flatMap((j) => [j.name, j.desc, j.why])));
  return [
    mapPanel(list),
    h('div', { class: 'cards' }, shown.map(card)),
    devices.length ? h('section', { class: 'panel devices' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Phones and other devices')),
      h('ul', {}, devices.filter((m) => matches(m.machine, m.role)).map(deviceRow))) : null,
  ];
}

function jobsView(list) {
  const pairs = list.flatMap((m) => (m.jobs || []).map((j) => ({ j, m: m.machine })));
  if (!pairs.length) return h('p', { class: 'muted empty' }, 'No machine reported any jobs yet.');
  const own = merge(pairs.filter((p) => p.j.own));
  const count = (f) => own.filter(f).length;
  const tiles = [['bad', count((it) => BAD_JOB.has(it.status)), 'need a look'], ['run', count((it) => it.status === 'running'), 'running now'],
    ['ok', count((it) => !BAD_JOB.has(it.status) && it.status !== 'running'), 'fine, waiting'],
    ['quiet', merge(pairs.filter((p) => !p.j.own)).length, 'built into the system']]
    .map(([st, nr, label]) => h('div', { class: `nm-tile st-${st}` }, h('span', { class: 'dot' }), h('strong', {}, String(nr)), h('span', {}, label)));
  const groups = jobGroups(pairs, 'all', true);
  return h('section', { class: 'panel jobs' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Jobs that run by themselves'),
      h('span', { class: 'sum' }, h('strong', {}, String(own.length)), ` of yours on ${new Set(pairs.filter((p) => p.j.own).map((p) => p.m)).size} machines`)),
    h('div', { class: 'nm-tiles' }, tiles),
    groups.length ? groups : h('p', { class: 'muted empty' }, 'No job matches.'));
}

// ------------------------------------------------------------------ names

const NAME_LABEL = { ok: 'Opens', api: 'No page', down: 'Down', dead: 'Dead', idle: 'Unused', off: 'Box off' };
const NAME_ST = { ok: 'ok', api: 'warn', down: 'bad', dead: 'offline', idle: 'warn', off: 'asleep' };
const KIND_LABEL = { app: 'app', preview: 'preview', policy: 'policy only' };

function used(n) {
  if (n.last_used) return now() - n.last_used < 120 ? 'in use now' : `used ${since(n.last_used)}`;
  if (n.kind === 'policy' || !n.served_by.length) return '';
  return n.used_known ? `no use seen in ${span(now() - n.first_seen)}` : 'use not tracked';
}

function nameRow(n) {
  const where = n.served_by.length ? n.served_by.map((s) => s.machine) : n.ready_on.length ? n.ready_on : n.hosts;
  const port = n.served_by.flatMap((s) => s.backends).map((b) => b.replace(/^https?:\/\/(127\.0\.0\.1|localhost)/, '')).join(', ');
  const link = n.kind === 'policy' ? h('span', { class: 'job-name' }, n.name)
    : h('a', { class: 'job-name nm-link', href: `http://${n.name}/`, target: '_blank', rel: 'noopener' }, n.name);
  return h('li', { class: `job nm st-${NAME_ST[n.state]}`, title: port ? `served on port ${port}` : null },
    h('div', { class: 'job-top' }, h('span', { class: 'dot' }), link,
      h('span', { class: 'nm-kind' }, KIND_LABEL[n.kind] || n.kind),
      where.map((w) => h('a', { class: 'job-m', href: `#/m/${encodeURIComponent(w)}` }, w)),
      h('span', { class: 'job-when' }, used(n))),
    h('div', { class: 'job-desc' }, [n.what && n.what !== n.name && !MACHINE_PREFIX.test(n.what) ? n.what : null, n.why].filter(Boolean).join(' · ')));
}

function namesView() {
  if (!names || !names.at) return h('p', { class: 'muted empty' }, 'The names check has not reported yet.');
  const all = (names.names || []).filter((n) => matches(n.name, n.what, n.why, ...n.hosts));
  const count = (st) => (names.names || []).filter((n) => n.state === st).length;
  const flags = (names.flags || []).filter((f) => !f.decided);  // answered: done, off the list
  const tiles = [['ok', 'open'], ['down', 'down'], ['dead', 'dead'], ['idle', 'unused']]
    .map(([st, label]) => h('div', { class: `nm-tile st-${NAME_ST[st]}` }, h('span', { class: 'dot' }),
      h('strong', {}, String(count(st))), h('span', {}, label)));
  const order = ['down', 'dead', 'idle', 'off', 'api', 'ok'];
  const groups = order.map((st) => [st, all.filter((n) => n.state === st)]).filter(([, g]) => g.length);
  return [
    h('section', { class: 'panel' },
      h('div', { class: 'panel-head' }, h('h2', {}, 'Tailnet names'),
        h('span', { class: 'sum' }, h('strong', {}, String((names.names || []).length)), ' names · checked ', since(names.at))),
      names.error ? h('p', { class: 'problems' }, `The last check failed ${since(names.error_at)}: ${names.error}`) : null,
      h('div', { class: 'nm-tiles' }, tiles),
      h('p', { class: 'nm-legend muted' }, 'Opens: the link works now. No page: the app runs, but it is an API for an app, with nothing to open in a browser. ',
        'Down: set up, but nothing answers. Dead: no box serves it any more. Unused: a preview nobody opened for two weeks. Box off: its box is switched off.'),
      flags.length ? h('div', { class: 'nm-flags' }, h('h3', {}, `To decide · ${flags.length}`),
        h('ul', {}, flags.map((f) => h('li', {}, h('span', {}, f.text),
          f.question_id ? h('a', { class: 'job-m', href: '/decisions' }, 'Decide →') : null)))) : null),
    ...groups.map(([st, g]) => h('section', { class: 'panel jobgroup' },
      st === 'dead' && !query  // long and already on the deck to decide: folded
        ? h('details', { class: 'more' }, h('summary', { class: 'nm-sum' }, `${NAME_LABEL[st]} · ${g.length} · tap to see`),
          h('ul', { class: 'job-list' }, g.map(nameRow)))
        : [h('div', { class: 'panel-head' }, h('h2', {}, `${NAME_LABEL[st]} · ${g.length}`)),
          h('ul', { class: 'job-list' }, g.map(nameRow))])),
  ];
}

function route() {
  const hash = location.hash.replace(/^#\/?/, '');
  if (hash.startsWith('login/')) return { view: 'map', machine: null, login: decodeURIComponent(hash.slice(6)) };
  return hash.startsWith('jobs') ? { view: 'jobs' } : hash.startsWith('names') ? { view: 'names' } : { view: 'map', machine: hash.startsWith('m/') ? decodeURIComponent(hash.slice(2)) : null };
}

const openMore = new Set();  // folds he opened (id) or closed (-id) stay so across the 30 s refresh

function render(scroll) {
  const r = route();
  for (const a of /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('#tabs a'))) a.classList.toggle('on', a.dataset.view === r.view);
  showLogins(r.view);
  if (!data) return;
  const list = data.machines || [];
  const bad = list.reduce((sum, m) => sum + badJobs(m).length, 0);
  $('bad-count').textContent = bad ? String(bad) : '';
  const flagged = names && names.flags ? names.flags.filter((f) => !f.decided).length : 0;
  $('names-count').textContent = flagged ? String(flagged) : '';
  const parts = [r.view === 'jobs' ? jobsView(list) : r.view === 'names' ? namesView() : overview(list)].flat().filter(Boolean);
  const view = $('view');
  if (r.view === 'map' && view.firstChild === parts[0]) {  // the live map stays put: only what follows it is redrawn
    while (parts[0].nextSibling) parts[0].nextSibling.remove();
    view.append(...parts.slice(1));
  } else view.replaceChildren(...parts);
  if (scroll) jumpTo(r);
}

function jumpTo(r) {  // a link into the page: one machine's card, or one box's Claude sign-in
  if (r.login) openLogin(r.login);
  const el = r.machine && document.getElementById(`m-${r.machine}`);
  if (el) { el.classList.add('flash'); el.scrollIntoView({ block: 'start' }); }
}

let scrolled = false;
async function load() {
  try {
    const [d, n, l] = await Promise.all([api('/api/machines'), api('/api/names').catch(() => names), api('/api/logins').catch(() => logins)]);
    d.fetchedAt = Date.now() / 1000;
    names = n;
    logins = l;
    snap.set('logins', l);
    snap.set('names', n);
    data = d;
    snap.set('machines', d);
    showError(null);
  } catch (e) {
    showError(e);
  }
  const typing = document.activeElement?.classList.contains('login-code');
  if (!typing) render(!scrolled);
  scrolled = true;
}

if (data) data.fetchedAt = data.fetchedAt || Date.now() / 1000;
render();  // last data first, no blank page
load();
setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
window.addEventListener('hashchange', () => render(true));
let resizeTimer, lastWidth = innerWidth;  // phones fire resize on scroll (the address bar): redraw only on a new width
window.addEventListener('resize', () => {
  if (innerWidth === lastWidth) return;
  lastWidth = innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => render(), 150);
});
$('search').addEventListener('input', (e) => { query = /** @type {HTMLInputElement} */ (e.target).value.trim().toLowerCase(); render(); });
document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== $('search')) { e.preventDefault(); $('search').focus(); }
  if (e.key === 'Escape' && selected) select(null);
});
