'use strict';

// ------------------------------------------------------------------ helpers

const $ = (id) => document.getElementById(id);
const SVG_NS = 'http://www.w3.org/2000/svg';

// Build DOM without innerHTML so prompt text can never become markup.
function h(tag, attrs, ...kids) {
  const el = attrs?.svg ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false || k === 'svg') continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'style') el.style.cssText = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const s = (tag, attrs, ...kids) => h(tag, { ...attrs, svg: true }, ...kids);
const svgText = (attrs, text) => s('text', attrs, text);

// Color follows the entity: fixed slots, never assigned by rank.
const slotColor = (n) => (n ? `var(--s${n})` : 'var(--other)');
const projectColor = (p) => slotColor(state.colors[p]);

// The recent top projects (from the hub) own a color in every view, so a color always
// means the same project; everything else stays gray rather than borrowing one.
// The period's busiest projects that hold no slot borrow one no project on screen uses,
// so the top of the day is never gray.
function assignColors(d) {
  const out = { ...state.meta.palette };
  const shown = (d?.by_project || []).filter((p) => !p.name.startsWith('(')).sort((a, b) => b.active_min - a.active_min);
  const taken = new Set(shown.map((p) => out[p.name]).filter(Boolean));
  const free = [1, 2, 3, 4, 5, 6, 7, 8].filter((n) => !taken.has(n));
  for (const p of shown.slice(0, 8)) if (!out[p.name] && free.length) out[p.name] = free.shift();
  return out;
}
// A session's name: its task's title or a short model-written one (the hub sets `title`),
// else the start of its first prompt, else the project.
function sessName(x) {
  if (x.title) return x.title;
  const q = (x.first_prompt || '').replace(/^\[(scheduled|mid-run)\] /, '').replace(/[`*_#>]/g, '').trim();
  if (!q) return pname(x.project);
  const cut = q.split(/(?<=[.!?])\s|\n/)[0];
  return cut.length > 48 ? cut.slice(0, 46).replace(/\s+\S*$/, '') + '…' : cut;
}
const swatch = (color) => h('i', { class: 'sw', style: `background:${color}` });

const nf = new Intl.NumberFormat('en-US');
const fmtInt = (n) => nf.format(Math.round(n || 0));
function fmtTok(n) {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}
function fmtUsd(n) {
  if (!n) return '$0';
  if (n < 100) return '$' + n.toFixed(2);
  return '$' + nf.format(Math.round(n));
}
// Axis ticks: whole dollars once they're big enough, so "$75" sits next to "$150", not "$75.00".
const fmtUsdAxis = (n) => (n >= 10 ? '$' + nf.format(Math.round(n)) : fmtUsd(n));
function fmtMin(m) {
  m = Math.round(m || 0);
  if (m < 60) return m + 'm';
  return Math.floor(m / 60) + 'h ' + String(m % 60).padStart(2, '0') + 'm';
}
let TZ = 'Europe/Prague';
const fmtTime = (ts) => new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }).format(new Date(ts * 1000));
const fmtDateTime = (ts) => new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { timeZone: TZ, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(ts * 1000));
// The date (in the hub's time zone) a timestamp falls on, as YYYY-MM-DD.
const localDate = (ts) => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ts * 1000));
const fmtDur = (sec) => (sec < 60 ? Math.round(sec) + 's' : fmtMin(sec / 60));
function fmtAgo(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 90) return 'just now';
  if (d < 3600) return Math.round(d / 60) + 'm ago';
  if (d < 86400 * 2) return Math.round(d / 3600) + 'h ago';
  return Math.round(d / 86400) + 'd ago';
}
function addDays(date, n) {
  const d = new Date(date + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
const weekday = (date) => (new Date(date + 'T12:00:00Z').getUTCDay() + 6) % 7; // Monday = 0
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
// Touch screens tap, they don't click.
const CLICK = matchMedia('(hover: none)').matches ? 'tap' : 'click';
const cap = (w) => w[0].toUpperCase() + w.slice(1);
const WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
function prettyDate(date, year) {
  return new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: year ? 'numeric' : undefined, timeZone: 'UTC' })
    .format(new Date(date + 'T12:00:00Z'));
}
const shortDate = (date) => new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'));
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  // Fine steps, so a $502 day tops out at $600, not $1,000 (the half line stays round too).
  return [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((m) => m * p).find((x) => x >= v);
}
const sum = (xs) => xs.reduce((a, b) => a + b, 0);
// Axis tops for minutes, in hours whose half is a whole hour too (so 0 / 12h / 24h, not 12.5h / 25h).
const niceHours = (m) => ([1, 2, 4, 6, 8, 12, 16, 20, 24, 30, 36, 48, 60, 72, 96].find((x) => x * 60 >= m) ?? Math.ceil(m / 1440) * 24) * 60;
// Lowercase without accents, the same as the hub's search: "ucet" finds "Účet".
const fold = (s) => (s || '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
// "1 prompt", "2 prompts".
const count = (n, word) => `${fmtInt(n)} ${word}${n === 1 ? '' : 's'}`;
const fmtShare = (f) => (f > 0 && f < 0.005 ? '<1%' : Math.round(f * 100) + '%');

async function api(path) {
  // A deploy restarts the hub for a few seconds and the proxy answers 502 meanwhile: wait it out quietly.
  let r;
  for (let tries = 5; ; ) {
    try {
      r = await fetch(path, { cache: 'no-store' });
      if (![502, 503, 504].includes(r.status) || --tries <= 0) break;
    } catch (e) {
      if (--tries <= 0) throw e;
    }
    await new Promise((ok) => setTimeout(ok, 1500));
  }
  if (!r.ok) throw new Error(`${path.split('?')[0]}: HTTP ${r.status}`);
  return r.json();
}

// Per-viewer UI preferences; storage can be missing or blocked.
const prefs = {
  get(k, d) { try { return localStorage.getItem('usage.' + k) || d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('usage.' + k, v); } catch { /* ignore */ } },
};

// ------------------------------------------------------------------ state

const VIEWS = ['day', '7', '30', '365'];
const FILTER_KEYS = ['agent', 'machine', 'project'];
const state = {
  view: 'day', date: null, filter: { agent: null, machine: null, project: null },
  lanes: prefs.get('lanes2', 'project'), trend: prefs.get('trend', 'cost'), trendBy: prefs.get('trendBy', 'project'),
  turnsSort: 'time', zoom: null, sessSort: null, sessAll: false, meta: null, data: null, query: '', queryKeys: new Map(), colors: {},
};
const isRange = () => state.view !== 'day';
const spanDays = () => (state.view === 'day' ? 1 : Number(state.view));

// A session's own address, so Tasks and Decisions can link straight to it: #/session/<id>.
// It opens today's view with that session's drawer on top.
function sessionFromHash() {
  const m = location.hash.match(/^#\/session\/(.+)$/);
  if (!m) return false;
  state.pendingSession = decodeURIComponent(m[1]);
  history.replaceState(null, '', '#/day');
  return true;
}

function readHash() {
  sessionFromHash();
  const [path, qs] = location.hash.slice(1).split('?');
  const [, view, date] = (path || '').split('/');
  // Any span of 2–731 days (a range dragged on a chart) is a view too, not only the tabs.
  state.view = VIEWS.includes(view) || (/^\d+$/.test(view || '') && +view >= 2 && +view <= 731) ? view : 'day';
  // A link without a date means today (load() asks the hub), even from an open page.
  // 2026-02-30 matches the pattern but isn't a day: treat it like no date.
  const real = /^\d{4}-\d{2}-\d{2}$/.test(date || '') && new Date(date + 'T12:00:00Z').toISOString().slice(0, 10) === date;
  state.date = real ? date : null;
  const p = new URLSearchParams(qs || '');
  for (const k of FILTER_KEYS) state.filter[k] = p.get(k) || null;
}
function filterQuery() {
  const p = new URLSearchParams();
  for (const k of FILTER_KEYS) if (state.filter[k]) p.set(k, state.filter[k]);
  return p.toString();
}
function writeHash(push) {
  const q = filterQuery();
  const hash = `#/${state.view}/${state.date}${q ? '?' + q : ''}`;
  if (location.hash === hash) return;
  if (push) history.pushState(null, '', hash);
  else history.replaceState(null, '', hash);
}

let seq = 0;
async function load({ push = false, quiet = false, follow = false, top = false } = {}) {
  const my = ++seq;
  // Dim the old view only when the hub is slow: a fast answer swaps without a flash.
  const dim = !quiet && state.data && setTimeout(() => { if (my === seq) $('main').classList.add('loading'); }, 180);
  try {
    const metaP = api('/api/meta');
    const q = filterQuery();
    const days = spanDays();
    let dataP;
    // Today's day view asks without a date (the hub means today), both calls at once instead of one after the other.
    if (days === 1 && (!state.date || follow)) dataP = api(`/api/day${q ? '?' + q : ''}`);
    else {
      // No date, following today, or a date after today (nothing there yet): today.
      // (UTC's date is at most a day behind the hub's, so only a date past it waits for meta.)
      if (!state.date || follow || state.date > new Date().toISOString().slice(0, 10)) {
        const today = (await metaP).today;
        if (my !== seq) return;  // the user navigated meanwhile
        if (!state.date || follow || state.date > today) state.date = today;
      }
      dataP = days === 1
        ? api(`/api/day?date=${state.date}${q ? '&' + q : ''}`)
        : api(`/api/range?from=${addDays(state.date, 1 - days)}&to=${state.date}${q ? '&' + q : ''}`);
    }
    const [meta, data] = await Promise.all([metaP, dataP]);
    if (my !== seq) return;
    if (days === 1 && data.date) state.date = data.date;
    if (days === 1 && !q && data.date === meta.today) usageSnap.set({ meta, data });
    state.meta = meta;
    TZ = meta.tz || TZ;
    state.data = data;
    writeHash(push);
    $('error').hidden = true;
    state.colors = assignColors(data);  // before the live strip, which colors pills by project
    if (state.pendingSession) { const id = state.pendingSession; state.pendingSession = null; setTimeout(() => openSession({ id }), 0); }
    renderHeader();
    renderLive();
    renderFilters();
    render();
    if (state.query) runSearch();  // the matches from later prompts belong to the old period
    if (top && window.scrollY > $('kpis').offsetTop) window.scrollTo({ top: 0 });
  } catch (e) {
    if (my !== seq) return;
    $('error').textContent = `Could not load data (${e.message}). Retrying every minute.`;
    $('error').hidden = false;
  } finally {
    clearTimeout(dim);
    if (my === seq) $('main').classList.remove('loading', 'booting');
  }
}

function go(changes, opts) {
  if ('date' in changes || 'view' in changes) { state.zoom = null; state.sessAll = false; state.sessSort = null; }
  const top = 'view' in changes && changes.view !== state.view;
  Object.assign(state, changes);
  if (state.meta && state.date > state.meta.today) state.date = state.meta.today;
  load({ push: true, top, ...opts });
}
function setFilter(key, value) {
  state.filter = { ...state.filter, [key]: state.filter[key] === value ? null : value };
  hideTip();
  load({ push: true });
}

// ------------------------------------------------------------------ header, live, filters

// The chosen button of a segmented control: shown by .on, announced by aria-pressed.
function markOn(id, isOn) {
  for (const b of $(id).querySelectorAll('button')) {
    b.classList.toggle('on', isOn(b));
    b.setAttribute('aria-pressed', String(isOn(b)));
  }
}

function renderHeader() {
  // A fresh hub with nothing pushed yet says how to feed it instead of showing a page of zeros.
  $('welcome').hidden = state.meta.events > 0;
  $('welcome-cmd').textContent = `python3 collector/collect.py --hub ${location.origin} --token <USAGE_TOKEN>`;
  markOn('view-tabs', (b) => b.dataset.view === state.view);
  const days = spanDays();
  const from = addDays(state.date, 1 - days);
  $('date-label').textContent = days === 1 ? prettyDate(state.date)
    : days > 30 ? `${shortDate(from)} ’${from.slice(2, 4)} – ${shortDate(state.date)} ’${state.date.slice(2, 4)}`
      : `${shortDate(from)} – ${shortDate(state.date)}`;
  const t = state.data.totals;
  document.title = `Beacon · Usage · ${$('date-label').textContent} · ${costText(t)}`;
  $('next').disabled = state.date >= state.meta.today;
  // Nothing older than the first tracked day exists; stepping further back only shows empties.
  $('prev').disabled = !!state.meta.first_date && from <= state.meta.first_date;
  $('today').disabled = state.date === state.meta.today;

  $('machines').replaceChildren(...state.meta.machines.map((m) => {
    const age = Date.now() / 1000 - m.last_seen;
    const cls = age < 1800 ? 'fresh' : age < 6 * 3600 ? 'stale' : 'dead';
    const files = Object.entries(m.counts || {}).filter(([, n]) => n).map(([a, n]) => `${a} ${n}`).join(', ');
    return h('button', {
      class: 'chip' + (state.filter.machine === m.name ? ' on' : ''),
      title: `last sync ${fmtDateTime(m.last_seen)}${files ? ` · files read last run: ${files}` : ''} · click to filter`,
      onclick: () => setFilter('machine', m.name),
    }, h('i', { class: `dot ${cls}` }), `${m.name} ${fmtAgo(m.last_seen).replace('just now', 'now').replace(' ago', '')}`);
  }));
}

// Running now: one tile per machine. A filled bubble is an agent working (it pulses), a ring is
// one waiting on you (only the last hour; older ones fold away). Names on hover.
const LIVE_RECENT_S = 3600;
const MACHINE_ICON = window.BEACON.machine_icons;  // settings `machines`, through /config.js
let liveAll = false;
function renderLive() {
  const live = state.meta.live || [];
  const now = Date.now() / 1000;
  const fresh = (x) => x.state === 'working' || (x.last && now - x.last < LIVE_RECENT_S);
  const shown = liveAll ? live : live.filter(fresh);
  const older = live.length - live.filter(fresh).length;
  const names = [...new Set([...state.meta.machines.map((m) => m.name), ...live.map((x) => x.machine)])];
  const tiles = names.map((name) => {
    const mine = shown.filter((x) => x.machine === name).sort((a, b) => (a.state === 'working' ? 0 : 1) - (b.state === 'working' ? 0 : 1) || (b.last || 0) - (a.last || 0));
    const work = mine.filter((x) => x.state === 'working').length;
    const m = state.meta.machines.find((q) => q.name === name);
    const online = m && Date.now() / 1000 - m.last_seen < 1800;
    return h('div', { class: 'mtile' + (work ? ' busy' : '') + (online ? '' : ' off') },
      h('div', { class: 'mt-head' }, h('span', { class: 'mt-ic' }, MACHINE_ICON[name] || '🖥️'),
        h('button', { class: 'flt mt-name', title: m ? `last sync ${fmtDateTime(m.last_seen)} · click to filter` : null, onclick: () => setFilter('machine', name) }, name),
        h('span', { class: 'mt-n' }, work ? `${work} working` : mine.length ? 'waiting' : online ? 'idle' : 'offline')),
      h('div', { class: 'mt-bubs' }, mine.length ? mine.map((x) => h('button', {
        class: 'av bub ' + (x.state === 'working' ? 'work' : 'wait'), style: `--c:${projectColor(x.project)}`,
        onclick: () => openSession(x),
        onmouseenter: (e) => showTip(e, [h('b', null, pname(x.project)),
          h('div', { class: 'muted' }, `${x.state === 'working' ? '⚡ working' : '⏸ waiting on you'} · ${fmtAgo(x.last)}`),
          x.prompt ? h('div', { style: 'margin-top:4px' }, shownPrompt(x.prompt).slice(0, 140)) : null]),
        onmousemove: moveTip, onmouseleave: hideTip,
      }, initials(x.project), h('i', { class: 'av-live ' + (x.state === 'working' ? 'work' : 'wait') }))) : h('span', { class: 'mt-empty' }, online ? '💤 nothing running' : '🔌 offline')));
  });
  $('live').hidden = !names.length;
  $('live').replaceChildren(h('div', { class: 'mtiles' }, tiles),
    older ? h('button', { class: 'live-more', onclick: () => { liveAll = !liveAll; renderLive(); } }, liveAll ? 'hide older' : `+${older} older waiting`) : '');
}

let filtersKey = '';
function renderFilters() {
  const key = JSON.stringify([state.meta.options, state.filter]);
  if (key === filtersKey) return;
  filtersKey = key;
  const labels = { agent: 'Agent', machine: 'Machine', project: 'Project' };
  const any = FILTER_KEYS.some((k) => state.filter[k]);
  $('filters').replaceChildren(
    ...FILTER_KEYS.map((k) => {
      const opts = [...(state.meta.options[k] || [])];
      if (state.filter[k] && !opts.includes(state.filter[k])) opts.unshift(state.filter[k]);
      const sel = h('select', {
        'aria-label': labels[k],
        onchange: (e) => { state.filter = { ...state.filter, [k]: e.target.value || null }; load({ push: true }); },
      }, h('option', { value: '' }, 'all'), opts.map((o) => h('option', { value: o }, k === 'project' ? pname(o) : o)));
      sel.value = state.filter[k] || '';
      return h('label', { class: 'sel' + (state.filter[k] ? ' on' : '') }, h('span', null, labels[k]), sel);
    }),
    any ? h('button', { class: 'clear', onclick: () => { state.filter = { agent: null, machine: null, project: null }; load({ push: true }); } }, 'Clear filters')
      : '',
  );
}

// ------------------------------------------------------------------ render

function render() {
  const d = state.data;
  const v = state.view;
  $('timeline-panel').hidden = v !== 'day';
  $('pace-panel').hidden = true;  // the cost KPI already says "vs average by now"; one less chart to read
  $('trend-panel').hidden = v === 'day';
  $('calendar-panel').hidden = v !== '365';
  $('hours-panel').hidden = v === 'day';
  renderKpis(d);
  if (v === 'day') renderTimeline(d); else renderTrend(d);
  if (v === '365') renderCalendar(d);
  if (v !== 'day') renderHours(d);
  renderProjects(d);
  renderAgents(d);
  renderModels(d.by_model);
  renderSessions();
}

function delta(cur, base, label) {
  if (base == null) return null;
  const vs = h('span', { class: 'vs' }, ` vs ${label}`);
  if (!base) return cur ? h('span', { class: 'delta' }, 'new', vs) : null;
  const r = cur / base;
  if (Math.abs(r - 1) < 0.01) return h('span', { class: 'delta' }, '≈ same', vs);
  const txt = r >= 10 ? '10×+' : r >= 2 ? `${r.toFixed(1)}×` : `${Math.round(Math.abs(r - 1) * 100)}%`;
  return h('span', { class: 'delta' }, `${r > 1 ? '↑' : '↓'} ${txt}`, vs);
}

// A share's change in percentage points: 69% to 73% is "4 pts", not "6%".
function deltaPts(cur, base, label) {
  if (cur == null || base == null) return null;
  const pts = Math.round((cur - base) * 100);
  const vs = h('span', { class: 'vs' }, ` vs ${label}`);
  return h('span', { class: 'delta' }, pts ? `${pts > 0 ? '↑' : '↓'} ${Math.abs(pts)} pts` : '≈ same', vs);
}

// A delta as a small colored pill: ▲ 75% (the comparison is in its tooltip).
function pill(cur, base, label) {
  if (base == null || cur == null) return null;
  if (!base) return cur ? h('span', { class: 'pill new', title: `nothing ${label}` }, 'new') : null;
  const r = cur / base;
  if (Math.abs(r - 1) < 0.03) return h('span', { class: 'pill same', title: `about the same as ${label}` }, '≈ usual');
  const txt = r >= 10 ? '10×+' : r >= 2 ? `${r.toFixed(1)}×` : `${Math.round(Math.abs(r - 1) * 100)}%`;
  return h('span', { class: 'pill ' + (r > 1 ? 'up' : 'down'), title: `vs ${label}` }, `${r > 1 ? '▲' : '▼'} ${txt}`);
}
function tile(icon, label, value, pillEl, pic, title) {
  return h('div', { class: 'kpi pic' },
    h('div', { class: 'k-top', title }, h('span', { class: 'k-ic' }, icon), h('span', { class: 'label' }, label), pillEl),
    h('div', { class: 'value' }, value), h('div', { class: 'k-pic' }, pic));
}

// Little pictures, each from data the page already has.
const SW = 240, SH = 56;
function areaPic(today, base, upTo) {
  const max = Math.max(0.01, ...today, ...base);
  const X = (i, n) => (i / Math.max(1, n - 1)) * SW, Y = (v) => SH - 3 - (v / max) * (SH - 8);
  const line = (xs) => xs.map((v, i) => `${i ? 'L' : 'M'}${X(i, base.length).toFixed(1)} ${Y(v).toFixed(1)}`).join('');
  const t = today.slice(0, upTo);
  return s('svg', { viewBox: `0 0 ${SW} ${SH}`, preserveAspectRatio: 'none', class: 'k-svg' },
    s('path', { d: line(base), style: 'fill:none;stroke:var(--faint);stroke-width:1.5;stroke-dasharray:4 3', 'vector-effect': 'non-scaling-stroke' }),
    t.length > 1 ? s('path', { d: `${line(t)}L${X(t.length - 1, base.length)} ${SH}L0 ${SH}Z`, style: 'fill:var(--s1);opacity:0.16' }) : null,
    t.length > 1 ? s('path', { d: line(t), style: 'fill:none;stroke:var(--s1);stroke-width:2.5', 'vector-effect': 'non-scaling-stroke' }) : null);
}
function columnsPic(values, color, labels) {
  const max = Math.max(1e-9, ...values), n = values.length, bw = SW / n;
  return s('svg', { viewBox: `0 0 ${SW} ${SH}`, preserveAspectRatio: 'none', class: 'k-svg' },
    values.map((v, i) => s('rect', { x: i * bw + bw * 0.15, width: bw * 0.7, y: SH - Math.max(v ? 2 : 0, (v / max) * SH), height: Math.max(v ? 2 : 0, (v / max) * SH),
      rx: Math.min(3, bw * 0.3), style: `fill:${color}` }, labels ? s('title', null, labels[i]) : null)));
}
// The day as 24 hour columns, taller the more minutes an agent ran in that hour. Night hours sit on a
// darker band, hours still to come are faded, and the axis reads like a clock: 00 06 12 18.
const isNight = (k) => k < 6 || k >= 22;
function hoursPic(perHour, nowHour) {
  const busiest = perHour.indexOf(Math.max(...perHour));
  const hh = (k) => String(k).padStart(2, '0') + ':00';
  return h('div', { class: 'h24' },
    h('div', { class: 'h24-bars' }, perHour.map((m, k) => {
      const future = nowHour != null && k > nowHour, now = nowHour != null && k === Math.floor(nowHour);
      return h('i', {
        class: ['h24-c', isNight(k) && 'night', future && 'future', now && 'now'].filter(Boolean).join(' '),
        'aria-label': `${hh(k)}: ${Math.round(m)} min`,
        ...tipOn(() => [h('b', null, `${hh(k)}–${hh(k + 1)}`), h('div', null, future ? 'not yet' : m ? `agents worked ${Math.round(m)} of 60 min` : 'nothing ran'),
          k === busiest && m ? h('div', { class: 'tip-detail' }, 'busiest hour') : null]),
      }, m ? h('b', { style: `height:${Math.max(8, Math.min(100, (m / 60) * 100)).toFixed(0)}%` }) : null);
    })),
    h('div', { class: 'h24-axis' }, [0, 6, 12, 18].map((k) => h('span', { style: `left:${(k / 24) * 100}%` },
      k === 0 ? '🌙 00' : k === 12 ? '☀️ 12' : String(k).padStart(2, '0')))));
}
// Hover, focus or tap shows the tooltip at once (a native title waits a second and never shows on a phone).
function tipOn(nodes) {
  return { tabindex: '0', onmouseenter: (e) => showTip(e, nodes()), onmousemove: moveTip, onmouseleave: hideTip,
    onfocus: (e) => showTip(e, nodes()), onblur: hideTip, onclick: (e) => { e.stopPropagation(); showTip(e, nodes()); } };
}
document.addEventListener('click', hideTip);
// Projects as bubbles, area ∝ agent time; the top three are named underneath.
function bubblesPic(rows) {
  const top = rows.slice(0, 9), max = Math.max(1, ...top.map((r) => r.active_min));
  const total = rows.reduce((a, r) => a + r.active_min, 0) || 1;
  const pct = (m) => `${Math.round((m / total) * 100)}% of agent time`;
  const rest = rows.slice(top.length);
  return h('div', { class: 'bubbles-wrap' }, h('div', { class: 'bubbles' }, top.map((r) => {
    const d = Math.round(10 + 34 * Math.sqrt(r.active_min / max));
    return h('i', { class: 'av', style: `width:${d}px;height:${d}px;--c:${projectColor(r.name)};font-size:${Math.round(d * 0.32)}px`, 'aria-label': `${pname(r.name)}: ${fmtMin(r.active_min)}`,
      ...tipOn(() => [h('b', null, pname(r.name)), h('div', null, `${fmtMin(r.active_min)} · ${pct(r.active_min)}`)]) }, d >= 24 ? initials(r.name) : null);
  }), rest.length ? h('em', tipOn(() => [h('b', null, `${rest.length} more`),
    ...rest.slice(0, 12).map((r) => tipRow(projectColor(r.name), fmtMin(r.active_min), pname(r.name))),
    rest.length > 12 ? h('div', null, `+${rest.length - 12} smaller`) : null]), `+${rest.length}`) : null),
  h('div', { class: 'bubbles-k' }, top.slice(0, 3).map((r) => h('span', null, h('i', { style: `background:${projectColor(r.name)}` }), pname(r.name)))));
}
// Sessions as dots: filled = you typed something, ring = it ran on its own; color = project. Batches count once.
function dotsPic(sessions) {
  const seen = new Set(), dots = [];
  for (const x of sessions) {
    const k = x.first_prompt ? `${x.project}|${x.first_prompt.slice(0, 60)}` : x.key;
    if (seen.has(k)) continue;
    seen.add(k);
    dots.push({ asked: x.prompts > 0 && !(x.first_prompt || '').startsWith('[scheduled]'), color: projectColor(x.project), x });
  }
  const shown = dots.slice(0, 84);
  const asked = dots.filter((d) => d.asked).length;
  const why = () => [h('b', null, `${dots.length} dots for ${count(sessions.length, 'session')}`),
    h('div', null, 'Sessions with the same first prompt in one project (a batch run) share one dot.'),
    h('div', null, 'Filled: you typed to it. Ring: it started on its own. Color: its project.')];
  return h('div', { class: 'dots-wrap' }, h('div', { class: 'dots' }, shown.map((d) => h('i', {
    class: d.asked ? 'on' : 'off', style: `--c:${d.color}`, 'aria-label': `${pname(d.x.project)}: ${sessName(d.x)}`,
    ...tipOn(() => [h('b', null, sessName(d.x)), tipRow(d.color, fmtTime(d.x.start), pname(d.x.project)),
      h('div', { class: 'tip-detail' }, d.asked ? 'you asked' : 'ran on its own')]) }))),
  h('div', { class: 'dots-k', ...tipOn(why) }, h('span', null, h('i', { class: 'on' }), `${asked} you asked`),
    h('span', null, h('i', { class: 'off' }), `${dots.length - asked} on their own`), h('span', { class: 'q' }, 'color = project')));
}

function renderKpis(d) {
  const t = d.totals;
  const day = !isRange();
  const base = day ? d.baseline : d.previous;
  const baseLabel = day ? 'a usual day by this time' : `the previous ${spanDays()} days`;
  const saved = Math.max(0, t.uncached - t.cost);
  const pricedMin = (x) => x.priced_min ?? x.active_min;
  const perHour = (x) => (x && pricedMin(x) > 0 && x.cost > 0 ? x.cost / (pricedMin(x) / 60) : null);
  const unknown = !t.cost && t.unpriced_calls > 0;
  const costTitle = [
    unknown ? `${count(t.unpriced_calls, 'call')} with no verified price.` : `List price of the tokens used. Without the prompt cache: ${fmtUsd(t.uncached)} (cache saved ${fmtUsd(saved)}).`,
    t.unpriced_calls && !unknown ? `Excludes ${count(t.unpriced_calls, 'unpriced call')}.` : null,
    perHour(t) != null ? `${fmtUsd(perHour(t))} per active hour.` : null,
    day ? 'Line: today so far. Dashes: a usual day.' : null,
  ].filter(Boolean).join('\n');
  const cum = (xs) => { let a = 0; return xs.map((v) => (a += v)); };
  const nowH = d.is_today ? Math.min(24, Math.ceil((Date.now() / 1000 - d.start) / 3600)) : 24;
  const buckets = !day && spanDays() > 90 ? months(d.days) : d.days;
  const blabel = (x) => (x.month ? x.date.slice(0, 7) : shortDate(x.date));
  // Minutes with an agent running, per hour (a day), or per day (a range).
  const perHourMin = day ? Array.from({ length: 24 }, (_, k) => d.concurrency.slice(k * 60, k * 60 + 60).filter((c) => c > 0).length) : null;
  const projects = d.by_project.filter((p) => p.active_min > 0).sort((a, b) => b.active_min - a.active_min);
  const sessTitle = [`${count(t.prompts, 'prompt')} · ${count(t.calls, 'model call')}.`,
    t.hands_off != null ? `Hands-off: ${fmtShare(t.hands_off)} of ${fmtTok(layerTotal(t))} tokens ran without you driving (${count(t.asks, 'ask')}, ${count(t.nudges, 'nudge')}, ${fmtInt(t.steers)} mid-run).` : null,
    day && t.peak ? `Peak: ${t.peak} agents at once at ${fmtTime(t.peak_ts)}.` : null, 'A batch of identical runs is one dot.'].filter(Boolean).join('\n');
  const items = [
    tile('💰', 'API value', unknown ? 'unpriced' : isFloor(t) ? [h('span', { class: 'muted' }, '≥ '), fmtUsd(t.cost)] : fmtUsd(t.cost),
      unknown ? null : pill(t.cost, base?.cost, baseLabel),
      day ? areaPic(cum(d.cost_by_hour), cum(d.baseline_cost_by_hour), nowH) : columnsPic(buckets.map((x) => x.cost || 0), 'var(--s1)', buckets.map((x) => `${blabel(x)} · ${fmtUsd(x.cost || 0)}`)),
      costTitle),
    tile('⏱️', 'Agents working', fmtMin(t.active_min), pill(t.active_min, base?.active_min, baseLabel),
      day ? hoursPic(perHourMin, d.is_today ? (Date.now() / 1000 - d.start) / 3600 : null)
        : columnsPic(buckets.map((x) => x.active_min || 0), 'var(--s3)', buckets.map((x) => `${blabel(x)} · ${fmtMin(x.active_min)}`)),
      'Minutes with at least one agent working.'),
    tile('📁', 'Projects', fmtInt(projects.length), pill(t.projects, base?.projects, baseLabel), bubblesPic(projects),
      projects[0] ? `Bubble size = agent time. Most: ${pname(projects[0].name)}.` : null),
    tile('💬', 'Sessions', fmtInt(t.sessions), pill(t.sessions, base?.sessions, baseLabel), dotsPic(d.sessions), sessTitle),
  ];
  $('kpis').replaceChildren(...items);
}

// ------------------------------------------------------------------ where the time went

// One bar of active time by project: the period's story in one line.
const SPLIT_TOP = 7;
function renderSplit(d) {
  const ps = d.by_project.filter((p) => p.active_min > 0).sort((a, b) => b.active_min - a.active_min);
  const total = sum(ps.map((p) => p.active_min));
  $('split-panel').hidden = !total;
  if (!total) return;
  const top = ps.slice(0, SPLIT_TOP);
  const rest = ps.slice(SPLIT_TOP);
  const segs = top.map((p) => ({ name: pname(p.name), min: p.active_min, cost: p.cost, color: projectColor(p.name), p }));
  if (rest.length) segs.push({ name: `${rest.length} more`, min: sum(rest.map((p) => p.active_min)), cost: sum(rest.map((p) => p.cost || 0)), color: 'var(--other)', more: true });
  const tip = (x) => [h('strong', null, x.name), h('div', null, `${fmtMin(x.min)} · ${Math.round((x.min / total) * 100)}% of agent time`),
    x.cost ? h('div', null, `${fmtUsd(x.cost)} API value`) : null];
  $('split').replaceChildren(
    h('div', { class: 'split-bar' }, segs.map((x) => {
      const share = x.min / total;
      return h('button', {
        class: 'seg-part' + (share < 0.07 ? ' narrow' : ''), style: `flex:${x.min} 1 0;background:${x.color}`,
        'aria-label': `${x.name}: ${fmtMin(x.min)}`,
        onmouseenter: (e) => showTip(e, tip(x)), onmousemove: moveTip, onmouseleave: hideTip,
        onclick: () => (x.more ? null : setFilter('project', x.name)),
      }, share >= 0.07 ? [h('span', { class: 'n' }, x.name), h('span', { class: 'v' }, `${Math.round(share * 100)}%`)] : null);
    })),
    h('div', { class: 'split-keys' }, segs.map((x) => h('span', null, h('i', { style: `background:${x.color}` }),
      x.name, h('b', null, fmtMin(x.min))))),
  );
}

// ------------------------------------------------------------------ timeline (day)

// The day's axis without the dead hours: every stretch of 30+ min with nothing running (and
// the rest of today after now) shrinks to a thin hatched sliver, and the working hours share
// the width. Returns ts → % and the pieces to draw.
const IDLE_MIN_S = 30 * 60, GAP_PCT = 3.5, PAD_S = 5 * 60;
function dayAxis(d) {
  const now = Date.now() / 1000;
  const lo = state.zoom ? state.zoom[0] : d.start;
  const end = state.zoom ? state.zoom[1] : d.is_today ? Math.min(d.end, now + 60) : d.end;
  const hi = state.zoom ? state.zoom[1] : d.end;
  const pad = state.zoom ? 0 : PAD_S;
  const spans = d.sessions.flatMap((x) => x.bursts).map(([a, b]) => [Math.max(lo, a - pad), Math.min(end, b + pad)])
    .filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0]);
  const merged = [];
  for (const [a, b] of spans) {
    const last = merged[merged.length - 1];
    if (last && a - last[1] < IDLE_MIN_S) last[1] = Math.max(last[1], b); else merged.push([a, b]);
  }
  if (!merged.length) merged.push([lo, end]);
  // Pieces in order: idle (fixed sliver) and active (share of the rest by duration).
  const pieces = [];
  let t = lo;
  for (const [a, b] of merged) {
    if (a - t >= 60) pieces.push({ idle: true, a: t, b: a });
    else if (pieces.length) pieces[pieces.length - 1].b = a;
    pieces.push({ idle: false, a: Math.max(t, a), b });
    t = b;
  }
  if (hi - t >= 60) pieces.push({ idle: true, a: t, b: hi, future: d.is_today && t >= now - 120 });
  const idleN = pieces.filter((p) => p.idle).length;
  const activeS = sum(pieces.filter((p) => !p.idle).map((p) => p.b - p.a)) || 1;
  const room = 100 - idleN * GAP_PCT;
  let x = 0;
  for (const p of pieces) { p.x = x; p.w = p.idle ? GAP_PCT : ((p.b - p.a) / activeS) * room; x += p.w; }
  const pct = (ts) => {
    if (ts <= lo) return 0;
    if (ts >= hi) return 100;
    const p = pieces.find((q) => ts <= q.b) || pieces[pieces.length - 1];
    if (p.idle) return p.x + p.w / 2;
    return p.x + (Math.min(Math.max(ts, p.a), p.b) - p.a) / Math.max(1, p.b - p.a) * p.w;
  };
  // % → time, for a drag on the timeline.
  const at = (x) => {
    const p = pieces.find((q) => x <= q.x + q.w) || pieces[pieces.length - 1];
    return p.idle ? (x < p.x + p.w / 2 ? p.a : p.b) : p.a + ((x - p.x) / Math.max(0.001, p.w)) * (p.b - p.a);
  };
  return { pct, at, pieces, lo, hi };
}

// Picking a window of the day: drag across the timeline (like a trading chart), or a quick button.
let timelineAxis = null;
function setZoom(z) { state.zoom = z; hideTip(); renderTimeline(state.data); }
function renderZoomChips(d) {
  const now = Date.now() / 1000;
  const last = Math.max(d.start + 3600, ...d.sessions.map((x) => x.end || 0));
  const until = d.is_today ? Math.min(d.end, now + 60) : Math.min(d.end, last + 300);
  const z = state.zoom;
  const quick = (label, hours) => {
    const win = [Math.max(d.start, until - hours * 3600), until];
    const on = z && Math.abs(z[0] - win[0]) < 90 && Math.abs(z[1] - win[1]) < 90;
    return h('button', { class: on ? 'on' : null, onclick: () => setZoom(win) }, label);
  };
  $('zoom-chips').replaceChildren(
    h('button', { class: z ? null : 'on', onclick: () => setZoom(null) }, 'Whole day'),
    quick(d.is_today ? 'Last 6 h' : 'Last 6 h', 6), quick('3 h', 3), quick('1 h', 1),
    z ? h('span', { class: 'zoom-now' }, `${fmtTime(z[0])} – ${fmtTime(z[1])}`) : h('span', { class: 'zoom-hint' }, 'or drag across the timeline'),
  );
}
// A drag over the bars picks that stretch; a short press stays a click on a bar.
(function brushTimeline() {
  const root = $('timeline');
  let start = null, box = null;
  const xPct = (e) => Math.min(100, Math.max(0, ((e.clientX - box.left) / box.width) * 100));
  root.addEventListener('pointerdown', (e) => {
    const track = e.target.closest('.lane-track, .conc svg');
    if (!track || e.button !== 0 || !timelineAxis) return;
    box = track.getBoundingClientRect();
    start = { x: e.clientX, p: xPct(e), moved: false };
  });
  addEventListener('pointermove', (e) => {
    if (!start) return;
    if (!start.moved && Math.abs(e.clientX - start.x) < 6) return;
    start.moved = true;
    hideTip();
    const b = $('brush'), r = root.getBoundingClientRect();
    const x0 = Math.min(start.x, e.clientX), x1 = Math.max(start.x, e.clientX);
    b.hidden = false;
    b.style.cssText = `left:${Math.max(box.left, x0) - r.left}px;width:${Math.min(box.right, x1) - Math.max(box.left, x0)}px`;
    b.textContent = `${fmtTime(timelineAxis.at(Math.min(start.p, xPct(e))))} – ${fmtTime(timelineAxis.at(Math.max(start.p, xPct(e))))}`;
  });
  addEventListener('pointerup', (e) => {
    if (!start) return;
    const s0 = start;
    start = null;
    if (!s0.moved) return;
    $('brush').hidden = true;
    const a = timelineAxis.at(Math.min(s0.p, xPct(e))), b = timelineAxis.at(Math.max(s0.p, xPct(e)));
    // The click that ends a drag must not open the bar under the pointer.
    addEventListener('click', (ev) => { ev.stopPropagation(); ev.preventDefault(); }, { capture: true, once: true });
    if (b - a >= 5 * 60) setZoom([a, b]);
  });
})();

const LANES_TOP = 7;
function renderTimeline(d) {
  const byProject = state.lanes === 'project';
  markOn('lanes-toggle', (b) => b.dataset.v === state.lanes);
  const axis = dayAxis(d);
  const { pct, pieces } = axis;
  timelineAxis = axis;
  renderZoomChips(d);
  // Zoomed: only what ran inside the window, clipped to it.
  if (state.zoom) {
    const [za, zb] = state.zoom;
    d = { ...d, sessions: d.sessions.map((x) => ({ ...x, bursts: x.bursts.filter(([a, b]) => b > za && a < zb).map(([a, b]) => [Math.max(a, za), Math.min(b, zb)]) }))
      .filter((x) => x.bursts.length) };
  }

  const projActive = Object.fromEntries(d.by_project.map((p) => [p.name, p]));
  const lanes = new Map();
  for (const sess of d.sessions) {
    const key = byProject ? sess.project : `${sess.machine}\u0000${sess.agent}`;
    if (!lanes.has(key)) lanes.set(key, { machine: sess.machine, agent: sess.agent, project: sess.project, sessions: [] });
    lanes.get(key).sessions.push(sess);
  }
  let order = [...lanes.values()].sort(byProject
    ? (a, b) => (projActive[b.project]?.active_min || 0) - (projActive[a.project]?.active_min || 0)
    : (a, b) => a.machine.localeCompare(b.machine) || a.agent.localeCompare(b.agent));
  if (byProject && order.length > LANES_TOP + 1) {
    const rest = order.slice(LANES_TOP);
    order = order.slice(0, LANES_TOP);
    order.push({ other: rest.map((l) => l.project), sessions: rest.flatMap((l) => l.sessions) });
  }

  // Ruler: hours inside the working stretches, the idle length on each sliver.
  const ticks = [];
  let lastX = -10;
  // Labels keep ~48px apart whatever the screen width.
  const trackPx = Math.max(200, ($('timeline').clientWidth || 1000) - (innerWidth <= 720 ? 112 : 200));
  const minGap = (48 / trackPx) * 100;
  for (const p of pieces) {
    if (p.idle) {
      const mins = (p.b - p.a) / 60;
      if (p.x + p.w / 2 - lastX < minGap) { lastX = Math.max(lastX, p.x + p.w); continue; }
      ticks.push(h('span', { class: 'idle-l', style: `left:${p.x + p.w / 2}%`, title: `${fmtTime(p.a)}–${fmtTime(p.b)} ${p.future ? 'still ahead' : 'nothing running'}` },
        p.future ? 'later' : [h('small', null, 'break'), fmtMin(mins).replace(/ 00m$/, 'h').replace(' ', '')]));
      lastX = p.x + p.w;
      continue;
    }
    const perHour = p.w / Math.max(1 / 60, (p.b - p.a) / 3600);  // % of width one hour takes here
    const step = perHour > 14 ? 1800 : perHour > 6 ? 3600 : perHour > 3 ? 7200 : 3 * 3600;
    const first = Math.ceil(p.a / step) * step;
    const marks = [p.a, ...Array.from({ length: Math.max(0, Math.floor((p.b - first) / step) + 1) }, (_, k) => first + k * step)];
    for (const ts of marks) {
      const x = pct(ts);
      if (ts > p.b || x - lastX < minGap) continue;
      ticks.push(h('span', { style: `left:${x}%` }, fmtTime(ts)));
      lastX = x;
    }
  }
  const rows = [h('div', { class: 'ruler' }, h('div'), h('div', { class: 'ticks' }, ticks))];
  const idleEls = () => pieces.filter((p) => p.idle).map((p) => h('div', { class: 'gap' + (p.future ? ' future' : ''), style: `left:${p.x}%;width:${p.w}%` }));

  for (const lane of order) {
    // One bar = agents were working on this project from here to there. It says how long, and
    // "×5" when five agents ran at once. Hovering shows each of them and what it was asked.
    const blocks = [];
    const parts = lane.sessions.flatMap((sess) => sess.bursts.map(([a, b]) => ({ a, b, sess }))).sort((x, y) => x.a - y.a);
    const merged = [];
    for (const p of parts) {
      const last = merged[merged.length - 1];
      if (last && p.a <= last.b + 60) { last.b = Math.max(last.b, p.b); last.parts.push(p); }
      else merged.push({ a: p.a, b: p.b, parts: [p] });
    }
    for (const m of merged) {
      const list = [...new Set(m.parts.map((p) => p.sess))];
      const main = list.reduce((x, y) => ((y.active_min || 0) > (x.active_min || 0) ? y : x));
      // Most agents at the same moment inside this bar.
      const ev = m.parts.flatMap((p) => [[p.a, 1], [p.b, -1]]).sort((x, y) => x[0] - y[0] || x[1] - y[1]);
      let cur = 0, peak = 0;
      for (const [, d] of ev) { cur += d; peak = Math.max(peak, cur); }
      m.peak = peak;
      const w = Math.max(0.35, pct(m.b) - pct(m.a));
      const px = (w / 100) * trackPx;
      const mins = (m.b - m.a) / 60;
      blocks.push(h('div', {
        class: 'burst', tabindex: 0, role: 'button',
        'aria-label': `${list.length} agent${list.length > 1 ? 's' : ''} in ${main.project}, ${fmtTime(m.a)} to ${fmtTime(m.b)}`,
        style: `left:${pct(m.a)}%;width:${w}%;top:11px;--c:${projectColor(main.project)}`,
        onmouseenter: (e) => showTip(e, spanTip(m, list, main)), onmousemove: moveTip, onmouseleave: hideTip,
        onclick: () => openSession(main), onkeydown: (e) => { if (e.key === 'Enter') openSession(main); },
      }, px > 64 ? h('span', { class: 'bl' }, fmtMin(mins)) : null,
      peak > 1 && px > 26 ? h('span', { class: 'bx' }, `×${peak}`) : null));
    }
    const track = h('div', { class: 'lane-track', style: 'height:46px' }, idleEls(), blocks);
    let label;
    if (lane.other) {
      label = h('div', { class: 'lane-label', title: lane.other.map(pname).join(', ') }, h('span', { class: 'tag muted' }, `${lane.other.length} more projects`),
        h('span', { class: 'sub' }, fmtMin(sum(lane.other.map((p) => projActive[p]?.active_min || 0)))));
    } else if (byProject) {
      label = h('div', { class: 'lane-label' },
        h('button', { class: 'flt tag', title: projectTitle(lane.project) || 'Filter to this project', onclick: () => setFilter('project', lane.project) },
          swatch(projectColor(lane.project)), pname(lane.project)),
        h('span', { class: 'sub' }, fmtMin(projActive[lane.project]?.active_min) + ' · ' + costText(projActive[lane.project] || { cost: 0 })));
    } else {
      label = h('div', { class: 'lane-label' },
        h('button', { class: 'flt tag', title: 'Filter to this machine', onclick: () => setFilter('machine', lane.machine) }, lane.machine),
        h('span', { class: 'sub' }, lane.agent));
    }
    rows.push(h('div', { class: 'lane' }, label, track));
  }
  if (!lanes.size) {
    rows.push(h('p', { class: 'muted' }, state.meta.first_date && state.date < state.meta.first_date
      ? `Before tracking began (${shortDate(state.meta.first_date)}).` : 'No agent activity on this day.'));
  }
  if (d.sessions.length) rows.push(concurrencyRow(d, pct, axis));
  rows.push(h('div', { class: 'brush', id: 'brush', hidden: true }));
  $('timeline').replaceChildren(...rows);
  roving($('timeline'), '.burst', FLAT);

  $('timeline-legend').replaceChildren(h('span', null, 'what agents worked on, and when'));
  $('timeline-note').textContent = '';
}

// Running total of the day's API value by hour, against the same curve for the average of the last 7 days.
function renderPace(d) {
  const el = $('pace');
  $('pace-panel').hidden = !d.cost_by_hour.some(Boolean) && !d.baseline_cost_by_hour.some(Boolean);  // nothing priced to chart
  if ($('pace-panel').hidden) return;
  const cum = (xs) => { let a = 0; return xs.map((v) => (a += v)); };
  const nowHour = d.is_today ? Math.min(24, (Date.now() / 1000 - d.start) / 3600) : 24;
  const today = cum(d.cost_by_hour).slice(0, Math.ceil(nowHour));
  const base = cum(d.baseline_cost_by_hour);
  const W = Math.max(240, el.clientWidth || 800), H = 170, padL = 48, padB = 22, padT = 8, padR = 12;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = niceMax(Math.max(0.01, ...today, ...base));
  const X = (hr) => padL + (hr / 24) * plotW, Y = (v) => padT + plotH * (1 - v / max);
  // Point k is the total at the end of hour k (today's last point sits at now); both start at 0 at midnight.
  const line = (xs, upto = 24) => 'M' + [[0, 0], ...xs.map((v, k) => [Math.min(k + 1, upto), v])]
    .map(([hr, v]) => `${X(hr).toFixed(1)} ${Y(v).toFixed(1)}`).join(' L');
  const svg = s('svg', { width: W, height: H, role: 'img', 'aria-label': 'API value through the day against the 7-day average' });
  for (const frac of [0, 0.5, 1]) {
    const y = padT + plotH * (1 - frac);
    svg.append(s('line', { x1: padL, x2: W - padR, y1: y + 0.5, y2: y + 0.5, style: 'stroke:var(--line);stroke-width:1' }));
    svg.append(svgText({ x: padL - 8, y: y + 4, 'text-anchor': 'end' }, fmtUsdAxis(max * frac)));
  }
  for (let hr = 0; hr <= 24; hr += 3) svg.append(svgText({ x: X(hr), y: H - 6, 'text-anchor': 'middle' }, String(hr).padStart(2, '0')));
  // Neutral ink: every palette color already means a project.
  svg.append(s('path', { d: line(base), style: 'fill:none;stroke:var(--muted);stroke-width:1.5;stroke-dasharray:4 4' }));
  if (today.length) svg.append(s('path', { d: line(today, nowHour), style: 'fill:none;stroke:var(--text);stroke-width:2.5;stroke-linejoin:round' }));
  for (let k = 0; k < 24; k++) {
    const tip = () => [h('b', null, `by ${String(k + 1).padStart(2, '0')}:00`),
      k < today.length ? tipRow('var(--text)', fmtUsd(today[k]), d.is_today ? 'today' : prettyDate(d.date)) : null,
      tipRow('var(--muted)', fmtUsd(base[k]), 'average of the previous 7 days')];
    svg.append(s('rect', { class: 'hit', x: X(k), y: padT, width: plotW / 24, height: plotH,
      onmouseenter: (e) => showTip(e, tip()), onmousemove: moveTip, onmouseleave: hideTip }));
  }
  el.replaceChildren(svg);
  // The average day up to the same minute (spend spread evenly inside each hour), not the same whole hour.
  const k = Math.floor(nowHour), frac = nowHour - k;
  const end = today.at(-1) || 0, same = (k ? base[k - 1] : 0) + (k < 24 ? frac * d.baseline_cost_by_hour[k] : 0);
  $('pace-note').textContent = !same ? ''
    : d.is_today ? `So far ${fmtUsd(end)} against ${fmtUsd(same)} on an average day by this time.`
      : `${fmtUsd(end)} for the day against ${fmtUsd(same)} on an average day of the week before.`;
}

// A timeline bar's hover card: when, how long, and one thin line per agent inside it, placed
// on the bar's own time, with what it was asked.
function spanTip(m, list, main) {
  const span = Math.max(60, m.b - m.a);
  const cost = sum(list.map((x) => x.cost || 0));
  const SHOW = 6;
  // Agents given the same prompt (an eval batch) share one line with a count.
  const groups = [];
  const byQ = new Map();
  for (const x of list.slice().sort((a, b) => a.start - b.start)) {
    const q = x.title || x.first_prompt ? sessName(x) : 'started on its own';
    if (!byQ.has(q)) { const g = { q, sess: [] }; byQ.set(q, g); groups.push(g); }
    byQ.get(q).sess.push(x);
  }
  const rows = groups.slice(0, SHOW).map((g) => {
    const mine = m.parts.filter((p) => g.sess.includes(p.sess));
    return h('div', { class: 'tc-row' },
      h('div', { class: 'tc-track' }, mine.map((p) => h('i', { style: `left:${((p.a - m.a) / span) * 100}%;width:${Math.max(2, ((p.b - p.a) / span) * 100)}%;background:${projectColor(p.sess.project)}` }))),
      h('div', { class: 'tc-q' }, g.sess.length > 1 ? h('b', { class: 'tc-n' }, `×${g.sess.length}`) : null, g.q));
  });
  return [
    h('div', { class: 'tc-head' }, swatch(projectColor(main.project)), h('span', null, pname(main.project)), h('em', null, fmtUsd(cost))),
    h('div', { class: 'tc-when' }, `${fmtTime(m.a)} – ${fmtTime(m.b)}`, h('span', null, fmtMin(span / 60))),
    h('div', { class: 'tc-sub' }, list.length > 1 ? `${list.length} agents${m.peak > 1 ? ` · up to ${m.peak} at once` : ''}` : `1 agent · ${main.machine}`),
    ...rows,
    groups.length > SHOW ? h('div', { class: 'tc-sub' }, `+ ${groups.length - SHOW} more`) : null,
    h('div', { class: 'tc-foot' }, CLICK === 'tap' ? 'Tap to open' : 'Click to open'),
  ];
}

function sessionTip(sess) {
  return [
    h('b', null, sessName(sess)),
    h('div', { class: 'muted' }, `${pname(sess.project)} · ${sess.agent}`),
    h('div', { class: 'muted' }, `${sess.machine} · ${fmtTime(sess.start)}–${fmtTime(sess.end)} · active ${fmtMin(sess.active_min)}`),
    h('div', null, `${sess.model || 'no model calls'} · ${count(sess.prompts, 'prompt')} · ${costText(sess)}`),
    sess.first_prompt ? h('div', { class: 'muted', style: 'margin-top:4px' }, shownPrompt(sess.first_prompt).slice(0, 180)) : null,
  ];
}

function concurrencyRow(d, pct, axis) {
  const peak = Math.max(0, ...d.concurrency), max = Math.max(1, peak);
  const W = 1000, H = 44;
  let path = `M0 ${H}`;
  d.concurrency.forEach((c, i) => {
    if (d.start + i * 60 < axis.lo || d.start + i * 60 >= axis.hi) return;
    const x0 = pct(d.start + i * 60) * 10, x1 = pct(d.start + (i + 1) * 60) * 10, y = H - (c / max) * (H - 4);
    path += ` L${x0.toFixed(1)} ${y.toFixed(1)} L${x1.toFixed(1)} ${y.toFixed(1)}`;
  });
  path += ` L${W} ${H} Z`;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' },
    s('path', { d: path, style: 'fill:var(--s1);opacity:0.35' }));
  return h('div', { class: 'conc' }, h('div', { class: 'lane-label' }, 'Agents at once', h('span', { class: 'sub' }, `peak ${peak}${d.totals.peak_ts ? ' at ' + fmtTime(d.totals.peak_ts) : ''}`)), svg);
}

// ------------------------------------------------------------------ tooltip

// A grid of hundreds of cells is one Tab stop, not hundreds: the arrow keys move inside it
// (and not the date while they do), Home and End jump to its ends.
const FLAT = { ArrowLeft: -1, ArrowRight: 1, Home: 0, End: 0 };
function roving(root, sel, keys, start = 0) {
  const cells = [...root.querySelectorAll(sel)];
  const first = start < 0 ? cells.length + start : start;
  cells.forEach((c, i) => c.setAttribute('tabindex', i === first ? '0' : '-1'));
  root.onkeydown = (e) => {
    const i = cells.indexOf(e.target);
    if (i < 0 || !(e.key in keys) || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    e.stopPropagation();
    const j = e.key === 'Home' ? 0 : e.key === 'End' ? cells.length - 1 : i + keys[e.key];
    if (!cells[j]) return;
    cells[i].setAttribute('tabindex', '-1');
    cells[j].setAttribute('tabindex', '0');
    cells[j].focus();
  };
}

function showTip(e, nodes) {
  const tip = $('tooltip');
  tip.replaceChildren(...nodes.filter(Boolean));
  tip.hidden = false;
  moveTip(e);
}
function moveTip(e) {
  const tip = $('tooltip');
  const x = e.clientX ?? e.target.getBoundingClientRect().right;
  const y = e.clientY ?? e.target.getBoundingClientRect().bottom;
  tip.style.left = Math.max(8, Math.min(window.innerWidth - tip.offsetWidth - 8, x + 14)) + 'px';
  tip.style.top = Math.max(8, Math.min(window.innerHeight - tip.offsetHeight - 8, y + 14)) + 'px';
}
function hideTip() { $('tooltip').hidden = true; }
const tipRow = (color, value, label) => h('div', { class: 'row' },
  h('i', { class: 'k', style: `background:${color}` }), h('strong', null, value), h('span', null, label));

// ------------------------------------------------------------------ per-day chart (7 / 30 days)

// Both metrics stack by project: the question is where the money and the time went.
// (By model family it was one color: Opus is nearly all of the value.)
// Agents are few and fixed, so each keeps one color; the legend names them.
const AGENT_COLORS = { 'Claude Code': 2, Codex: 1, Antigravity: 3, OpenCode: 4, Muse: 7, Grok: 8 };
const agentColor = (a) => slotColor(AGENT_COLORS[a]);

// Who drove the tokens (hub/store.py aggregate()), from most hands-on to most autonomous.
const LAYERS = [
  { key: 'tokens_unattended', label: 'Unattended (nobody typed it)', color: 'var(--s1)' },
  { key: 'tokens_subagent', label: 'Subagents', color: 'var(--s7)' },
  { key: 'tokens_solo', label: 'Agent alone', color: 'var(--s3)' },
  { key: 'tokens_driven', label: 'You driving', color: 'var(--s2)' },
];
const layerTotal = (x) => sum(LAYERS.map((l) => x[l.key] || 0));
const messages = (x) => (x.asks || 0) + (x.nudges || 0) + (x.steers || 0);
const DRIVEN_CALLS = 20;

function foldSeries(buckets) {
  if (state.trendBy === 'autonomy') {
    const share = (i, v) => (layerTotal(buckets[i]) ? fmtShare(v / layerTotal(buckets[i])) : '');
    return {
      series: LAYERS.map((l) => ({ label: l.label, color: l.color, values: buckets.map((x) => x[l.key] || 0),
        detail: (i) => share(i, buckets[i][l.key] || 0) })),
      fmt: fmtTok, total: (i) => layerTotal(buckets[i]),
      totalText: (i) => `${fmtTok(layerTotal(buckets[i]))}${buckets[i].hands_off != null ? ` · ${fmtShare(buckets[i].hands_off)} hands-off` : ''}`,
      totalLabel: 'tokens',
    };
  }
  const cost = state.trend === 'cost';
  const fmts = cost
    ? { fmt: fmtUsd, axis: fmtUsdAxis, total: (i) => buckets[i].cost, totalText: (i) => costText(buckets[i]), totalLabel: 'API value' }
    : { fmt: fmtMin, axis: (m) => `${+(m / 60).toFixed(1)}h`, nice: niceHours, total: (i) => buckets[i].active_min, totalLabel: 'wall-clock active' };
  if (state.trendBy === 'agent') {
    const key = cost ? 'cost_by_agent' : 'active_by_agent';
    const agents = [...new Set(buckets.flatMap((x) => Object.keys(x[key] || {}).filter((a) => x[key][a] > 0)))]
      .sort((a, b) => sum(buckets.map((x) => x[key][b] || 0)) - sum(buckets.map((x) => x[key][a] || 0)));
    return { series: agents.map((a) => ({ label: a, color: agentColor(a), values: buckets.map((x) => x[key][a] || 0) })), ...fmts };
  }
  const key = cost ? 'cost_by_project' : 'active_by_project';
  const colored = Object.keys(state.colors).sort((a, b) => state.colors[a] - state.colors[b])
    .filter((p) => buckets.some((x) => x[key][p]));
  const series = colored.map((p) => ({ label: pname(p), color: projectColor(p), values: buckets.map((x) => x[key][p] || 0) }));
  const rest = buckets.map((x) => Object.entries(x[key]).filter(([p]) => !state.colors[p] && x[key][p] > 0).sort((a, b) => b[1] - a[1]));
  const other = rest.map((r) => sum(r.map(([, v]) => v)));
  // The tooltip names what is inside the gray, which in a long view is often most of it.
  const detail = (i) => (rest[i].length ? rest[i].slice(0, 3).map(([p]) => p).join(', ') + (rest[i].length > 3 ? ` +${rest[i].length - 3}` : '') : '');
  if (other.some(Boolean)) series.push({ label: 'Other & unnamed', color: 'var(--other)', values: other, detail });
  return { series, ...fmts };
}

// The year's days summed per calendar month, in the shape stackedColumns reads.
function months(days) {
  const out = new Map();
  for (const x of days) {
    const k = x.date.slice(0, 7);
    if (!out.has(k)) {
      out.set(k, { date: k + '-01', month: true, last: x.date, cost: 0, active_min: 0, prompts: 0, sessions: 0, calls: 0, unpriced_calls: 0,
        asks: 0, nudges: 0, steers: 0, ...Object.fromEntries(LAYERS.map((l) => [l.key, 0])),
        cost_by_project: {}, active_by_project: {}, cost_by_agent: {}, active_by_agent: {} });
    }
    const m = out.get(k);
    m.last = x.date;
    m.cost += x.cost; m.active_min += x.active_min; m.prompts += x.prompts;
    m.calls += x.calls; m.unpriced_calls += x.unpriced_calls;
    for (const f of ['asks', 'nudges', 'steers', ...LAYERS.map((l) => l.key)]) m[f] += x[f] || 0;
    for (const f of ['cost_by_project', 'active_by_project', 'cost_by_agent', 'active_by_agent']) {
      for (const [p, v] of Object.entries(x[f] || {})) m[f][p] = (m[f][p] || 0) + v;
    }
  }
  for (const m of out.values()) {
    const all = layerTotal(m);
    m.hands_off = all ? (all - (m.tokens_driven || 0)) / all : null;
  }
  // Months before tracking began are empty: start at the first month with anything in it.
  const list = [...out.values()];
  const first = list.findIndex((m) => m.cost || m.active_min);
  return first < 0 ? list : list.slice(first);
}

function renderTrend(d) {
  markOn('trend-toggle', (b) => b.dataset.v === state.trend);
  markOn('trend-by', (b) => b.dataset.v === state.trendBy);
  const autonomy = state.trendBy === 'autonomy';
  // That chart is always tokens; on the year view the toggle still drives the calendar below.
  $('trend-toggle').hidden = autonomy && spanDays() <= 90;
  const by = state.trendBy === 'agent' ? 'agent' : 'project';
  const year = spanDays() > 90;
  const buckets = year ? months(d.days) : d.days;
  const cfg = foldSeries(buckets);
  const per = year ? 'month' : 'day';
  $('trend-title').textContent = autonomy ? `Tokens per ${per}, by who drove them`
    : state.trend === 'cost' ? `API value per ${per}, by ${by}` : `Active time per ${per}, by ${by}`;
  $('trend-legend').replaceChildren(...(cfg.series.length > 1 ? cfg.series : []).map((x) => h('span', null, h('i', { style: `background:${x.color}` }), x.label)));
  $('trend-note').textContent = (autonomy
    ? `You driving: the first ${DRIVEN_CALLS} main-thread calls after each message you typed (a prompt, or one sent mid-run). `
      + 'Agent alone: its calls after that. Subagents: what your sessions spawned. Unattended: turns nobody typed (/loop, cron, headless runs, the task agent). '
      + 'Tokens count cache reads, so long runs weigh most. '
    : state.trend === 'active'
      ? `Two ${by}s running at once both count, so a ${per}'s stack can exceed its wall-clock active time (shown in the tooltip). ` : '')
    + `${cap(CLICK)} a ${per} to open it, or drag across ${per}s to see just those.`;
  if (!buckets.some((x) => x.cost || x.active_min)) {
    const before = state.meta.first_date && state.date < state.meta.first_date;
    $('trend').replaceChildren(h('p', { class: 'empty' }, before
      ? `Nothing yet: tracking began ${shortDate(state.meta.first_date)}.` : 'No agent activity in this period.'));
    $('trend-legend').replaceChildren();
    $('trend-note').textContent = '';
    return;
  }
  if (state.trend === 'cost' && !buckets.some((x) => x.cost) && d.totals.unpriced_calls) {
    $('trend').replaceChildren(h('p', { class: 'empty' }, 'No priced calls in this view: these agents log no cost and their models have no verified price. Switch to Active time.'));
    $('trend-legend').replaceChildren();
    $('trend-note').textContent = '';
    return;
  }
  stackedColumns($('trend'), buckets, cfg);
}

function roundedTop(x, y, w, hgt, r) {
  r = Math.min(r, hgt, w / 2);
  return `M${x} ${y + hgt}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + hgt}Z`;
}

function stackedColumns(el, days, { series, fmt, axis = fmt, nice = niceMax, total, totalText = (i) => fmt(total(i)), totalLabel }) {
  const W = Math.max(240, el.clientWidth || 800), H = 220, padL = 48, padB = 22, padT = 8;
  const plotH = H - padB - padT;
  const stackTotals = days.map((_, i) => sum(series.map((x) => x.values[i])));
  const max = nice(Math.max(0, ...stackTotals));
  const slot = (W - padL) / days.length;
  const bw = Math.max(2, Math.min(days.length <= 10 ? 64 : 24, slot * 0.68));
  const svg = s('svg', { width: W, height: H, role: 'group', 'aria-label': days[0]?.month ? 'Stacked columns per month' : 'Stacked columns per day' });
  for (const frac of [0, 0.5, 1]) {
    const y = padT + plotH * (1 - frac);
    svg.append(s('line', { x1: padL, x2: W, y1: y + 0.5, y2: y + 0.5, style: `stroke:var(--line);stroke-width:1` }));
    svg.append(svgText({ x: padL - 8, y: y + 4, 'text-anchor': 'end' }, axis(max * frac)));
  }
  const every = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor((W - padL) / 64))));
  const todayLast = days.at(-1)?.date === state.meta.today && !days.at(-1).month;
  const monthName = (x) => new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { month: 'short', timeZone: 'UTC' }).format(new Date(x.date + 'T12:00:00Z'));
  days.forEach((day, i) => {
    const x = padL + i * slot + (slot - bw) / 2;
    let base = padT + plotH;
    // The current day or month isn't over: drawn lighter, labelled "so far".
    const partial = day.month ? day.last === state.meta.today : day.date === state.meta.today;
    const segs = series.map((x2, k) => ({ k, v: x2.values[i] })).filter((g) => g.v > 0);
    segs.forEach((g, j) => {
      const full = (g.v / max) * plotH;
      const gap = j > 0 && full > 3 ? 2 : 0; // surface gap between stacked segments
      const top = base - full;
      const hgt = full - gap;
      if (hgt > 0) {
        const style = `fill:${series[g.k].color}${partial ? ';opacity:0.5' : ''}`;
        svg.append(j === segs.length - 1
          ? s('path', { d: roundedTop(x, top, bw, hgt, 4), style })
          : s('rect', { x, y: top, width: bw, height: hgt, style }));
      }
      base = top;
    });
    const clearOfToday = !todayLast || days.length - 1 - i >= every;  // keep room for the "today" label
    if ((i % every === 0 && clearOfToday) || days.length <= 10 || partial) {
      // The last label hangs off the right edge on a narrow chart unless it ends at its column.
      const last = i === days.length - 1 && slot < 40;
      svg.append(svgText({ x: padL + i * slot + (last ? slot : slot / 2), y: H - 6, 'text-anchor': last ? 'end' : 'middle' },
        day.month ? monthName(day) : partial ? 'today' : shortDate(day.date)));
    }
    const when = (day.month ? `${monthName(day)} ${day.date.slice(0, 4)}` : prettyDate(day.date)) + (partial ? ' · so far' : '');
    const tip = () => [
      h('b', null, when),
      tipRow('var(--text)', totalText(i), totalLabel),
      ...segs.slice().reverse().flatMap((g) => [tipRow(series[g.k].color, fmt(g.v), series[g.k].label),
        series[g.k].detail ? h('div', { class: 'muted tip-detail' }, series[g.k].detail(i)) : null]),
      h('div', { class: 'muted', style: 'margin-top:4px' },
        day.month ? count(day.prompts, 'prompt') : `${count(day.sessions, 'session')} · ${count(day.prompts, 'prompt')}`),
    ];
    const open = () => go(day.month ? { view: '30', date: day.last } : { view: 'day', date: day.date });
    svg.append(s('rect', {
      class: 'hit', x: padL + i * slot, y: padT, width: slot, height: plotH, tabindex: 0,
      role: 'button', 'aria-label': `${when}: ${totalText(i)} ${totalLabel}`,
      onmouseenter: (e) => showTip(e, tip()), onmousemove: moveTip, onmouseleave: hideTip,
      onfocus: (e) => showTip(e, tip()), onblur: hideTip,
      onclick: () => { hideTip(); open(); },
      onkeydown: (e) => { if (e.key === 'Enter') open(); },
    }));
  });
  // Drag across columns to open exactly that stretch of days.
  const brush = s('rect', { class: 'col-brush', y: padT, height: plotH, x: 0, width: 0, style: 'display:none' });
  svg.append(brush);
  const idx = (e) => Math.min(days.length - 1, Math.max(0, Math.floor((e.clientX - svg.getBoundingClientRect().left - padL) / slot)));
  let from = null, moved = false;
  svg.addEventListener('pointerdown', (e) => { if (e.button === 0) { from = idx(e); moved = false; } });
  svg.addEventListener('pointermove', (e) => {
    if (from == null) return;
    const i = idx(e);
    if (i === from && !moved) return;
    moved = true;
    hideTip();
    const a = Math.min(from, i), b = Math.max(from, i);
    brush.setAttribute('x', padL + a * slot); brush.setAttribute('width', (b - a + 1) * slot); brush.style.display = '';
  });
  const finish = (e) => {
    if (from == null) return;
    const a = Math.min(from, idx(e)), b = Math.max(from, idx(e));
    from = null;
    brush.style.display = 'none';
    if (!moved || a === b) return;
    addEventListener('click', (ev) => { ev.stopPropagation(); ev.preventDefault(); }, { capture: true, once: true });
    const first = days[a].date, lastDay = days[b].month ? days[b].last : days[b].date;
    const n = Math.round((Date.parse(lastDay + 'T12:00:00Z') - Date.parse(first + 'T12:00:00Z')) / 86400000) + 1;
    go(n === 1 ? { view: 'day', date: lastDay } : { view: String(Math.min(731, n)), date: lastDay });
  };
  svg.addEventListener('pointerup', finish);
  svg.addEventListener('pointerleave', (e) => { if (from != null && moved) finish(e); });
  el.replaceChildren(svg);
  roving(svg, '.hit', FLAT, -1);
}

// ------------------------------------------------------------------ heat scales

// Intensity, not rank: each step is a fifth of the busiest cell, so twice as dark means
// about twice the time (rank steps painted a 40-minute hour like a 2-hour one).
// Daily values are heavy-tailed (one $500 day), so the calendar steps by square root instead.
function levels(values, power = 1) {
  const max = Math.max(0, ...values);
  return (v) => (v <= 0 || !max ? 0 : 1 + Math.min(4, Math.floor((v / max) ** power * 5)));
}

function scaleLegend(el, extra) {
  el.replaceChildren(...[h('span', { class: 'gap-r' }, 'Less'), ...[0, 1, 2, 3, 4, 5].map((q) => h('i', { style: `background:var(--q${q})` })),
    h('span', { class: 'gap-l' }, 'More'), extra ? h('span', { class: 'muted', style: 'margin-left:14px' }, extra) : null].filter(Boolean));
}

// ------------------------------------------------------------------ year calendar

function renderCalendar(d) {
  // One metric toggle (on the monthly chart above) drives both year charts.
  const cost = state.trend === 'cost';
  $('calendar-title').textContent = cost ? 'API value per day' : 'Active time per day';
  const val = (x) => (cost ? x.cost : x.active_min);
  const fmt = cost ? fmtUsd : fmtMin;
  const level = levels(d.days.map(val), 0.5);
  const wd0 = weekday(d.days[0].date);
  const cols = Math.ceil((d.days.length + wd0) / 7);
  const scroll = $('calendar-scroll');
  const labelW = 30, top = 18, gap = 3;
  const cell = Math.max(10, Math.min(20, Math.floor((scroll.clientWidth - labelW) / cols) - gap));
  const W = labelW + cols * (cell + gap), H = top + 7 * (cell + gap);
  const svg = s('svg', { width: W, height: H, role: 'group', 'aria-label': 'Activity per day over the year' });
  [0, 2, 4].forEach((r) => svg.append(svgText({ x: 0, y: top + r * (cell + gap) + cell - 2 }, WEEKDAYS[r])));
  d.days.forEach((day, i) => {
    // Label a month over the column holding its 1st; the partial first month only if it has room.
    if (!(day.date.endsWith('-01') || (i === 0 && Number(day.date.slice(8)) <= 21))) return;
    const col = Math.floor((i + wd0) / 7);
    if (col < cols - 1) svg.append(svgText({ x: labelW + col * (cell + gap), y: 11 },
      new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { month: 'short', timeZone: 'UTC' }).format(new Date(day.date + 'T12:00:00Z'))));
  });
  d.days.forEach((day, i) => {
    const col = Math.floor((i + wd0) / 7), row = (i + wd0) % 7;
    const x = labelW + col * (cell + gap), y = top + row * (cell + gap);
    const tip = () => [h('b', null, prettyDate(day.date, true)),
      tipRow('var(--q4)', cost ? costText(day) : fmt(val(day)), cost ? 'API value' : 'agent-active'),
      h('div', { class: 'muted' }, cost ? `active ${fmtMin(day.active_min)}` : `${costText(day)} API value`),
      h('div', { class: 'muted' }, `${count(day.sessions, 'session')} · ${count(day.prompts, 'prompt')}`)];
    svg.append(s('rect', {
      // Worked but unpriced has no API value to shade, yet it wasn't an idle day: outlined instead.
      class: ['cell', day.date === state.meta.today && 'today', cost && !day.cost && day.active_min > 0 && 'unpriced'].filter(Boolean).join(' '),
      x, y, width: cell, height: cell, rx: 2,
      style: `fill:var(--q${level(val(day))})`, tabindex: 0, role: 'button',
      'aria-label': `${prettyDate(day.date, true)}: ${costText(day)} API value, active ${fmtMin(day.active_min)}`,
      onmouseenter: (e) => showTip(e, tip()), onmousemove: moveTip, onmouseleave: hideTip,
      onfocus: (e) => showTip(e, tip()), onblur: hideTip,
      onclick: () => { hideTip(); go({ view: 'day', date: day.date }); },
      onkeydown: (e) => { if (e.key === 'Enter') go({ view: 'day', date: day.date }); },
    }));
  });
  $('calendar').replaceChildren(svg);
  // Columns are weeks: left and right move a week, up and down a day.
  roving(svg, '.cell', { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1, Home: 0, End: 0 }, -1);
  // Start at the latest week, but let a refresh of the same year keep where the viewer scrolled.
  const calKey = `${d.from}|${d.to}|${W}`;
  if (scroll.dataset.key !== calKey) scroll.scrollLeft = scroll.scrollWidth;
  scroll.dataset.key = calKey;

  let best = 0, run = 0;
  for (const x of d.days) { run = x.active_min > 0 ? run + 1 : 0; best = Math.max(best, run); }
  let current = 0;
  let i = d.days.length - 1;
  if (d.days[i]?.date === state.meta.today && !d.days[i].active_min) i--;  // today isn't over yet
  for (; i >= 0 && d.days[i].active_min > 0; i--) current++;
  const busiest = d.days.reduce((a, b) => (val(b) > val(a) ? b : a), d.days[0]);
  const active = d.days.filter((x) => x.active_min > 0).length;
  $('calendar-note').textContent = `${active} active days · current streak ${current} · longest ${best}`;
  scaleLegend($('calendar-scale'), val(busiest) ? `busiest: ${shortDate(busiest.date)} (${fmt(val(busiest))})` : null);
  if (cost && d.days.some((x) => !x.cost && x.active_min > 0)) {
    $('calendar-scale').append(h('span', { class: 'muted', style: 'margin-left:14px' }, h('i', { class: 'unpriced-key' }), ' active, unpriced'));
  }
}

// ------------------------------------------------------------------ weekday x hour

function renderHours(d) {
  const el = $('hours');
  const counts = [0, 0, 0, 0, 0, 0, 0];
  // Averages are over tracked days: weeks before the first log would dilute them.
  for (const x of d.days) if (!state.meta.first_date || x.date >= state.meta.first_date) counts[weekday(x.date)]++;
  const level = levels(d.hours.flat());
  const labelW = 34, top = 16, gap = 2;
  const cw = Math.max(8, Math.min(56, Math.floor((el.clientWidth - labelW) / 24) - gap));
  const ch = Math.min(20, Math.max(12, cw * 0.5));
  const W = labelW + 24 * (cw + gap), H = top + 7 * (ch + gap);
  const svg = s('svg', { width: W, height: H, role: 'group', 'aria-label': 'Active minutes by weekday and hour' });
  for (let hr = 0; hr < 24; hr += 3) svg.append(svgText({ x: labelW + hr * (cw + gap), y: 11 }, String(hr).padStart(2, '0')));
  d.hours.forEach((row, wd) => {
    svg.append(svgText({ x: 0, y: top + wd * (ch + gap) + ch - 3 }, WEEKDAYS[wd]));
    row.forEach((m, hr) => {
      const tip = () => [h('b', null, `${WEEKDAYS[wd]} ${String(hr).padStart(2, '0')}:00–${String(hr + 1).padStart(2, '0')}:00`),
        tipRow('var(--q4)', fmtMin(m), counts[wd] === 1 ? `active that ${WEEKDAY_NAMES[wd]}` : counts[wd] ? `active across ${counts[wd]} ${WEEKDAY_NAMES[wd]}s` : 'active'),
        counts[wd] > 1 && m > 0 ? h('div', { class: 'muted' }, `${fmtMin(m / counts[wd])} on an average ${WEEKDAY_NAMES[wd]}`) : null];
      svg.append(s('rect', {
        class: 'cell', x: labelW + hr * (cw + gap), y: top + wd * (ch + gap), width: cw, height: ch, rx: 2,
        style: `fill:var(--q${level(m)});cursor:default`, tabindex: 0, role: 'img',
        'aria-label': `${WEEKDAY_NAMES[wd]} ${String(hr).padStart(2, '0')}:00: ${fmtMin(m)} active`,
        onmouseenter: (e) => showTip(e, tip()), onmousemove: moveTip, onmouseleave: hideTip,
        onfocus: (e) => showTip(e, tip()), onblur: hideTip,
      }));
    });
  });
  el.replaceChildren(svg);
  roving(svg, '.cell', { ...FLAT, ArrowUp: -24, ArrowDown: 24 });
  scaleLegend($('hours-scale'));
}

// ------------------------------------------------------------------ tables

// Click a header to sort by it (again to flip); `limit` shows the first rows and a "show all" row.
const sorts = {};
// `initial` names the order the rows already come in, so its header shows the arrow.
function table(el, cols, rows, onClick, empty = 'Nothing here.', limit = 0, initial = null) {
  const cls = (c, extra) => [c.r ? 'r' : null, c.hideSm ? 'hide-sm' : null, c.hide ? `hide-${c.hide}` : null,
    c.sort ? 'sortable' : null, extra].filter(Boolean).join(' ') || null;
  const redraw = () => table(el, cols, rows, onClick, empty, limit, initial);
  const picked = sorts[el.id];
  const st = picked && cols.some((c) => c.sort && c.label === picked.label) ? picked : initial;
  const col = st && cols.find((c) => c.label === st.label && c.sort);
  let list = rows;
  if (col) {
    list = rows.slice().sort((a, b) => {
      const x = col.sort(a), y = col.sort(b);
      return (typeof x === 'string' ? x.localeCompare(y) : x - y) * st.dir;
    });
  }
  const period = `${state.view}|${state.date}`;
  const shown = limit && el.dataset.all !== period && list.length > limit + 1 ? list.slice(0, limit) : list;
  const head = h('thead', null, h('tr', null, cols.map((c) => h('th', {
    class: cls(c), 'aria-sort': col === c ? (st.dir > 0 ? 'ascending' : 'descending') : null,
    tabindex: c.sort ? 0 : null, title: c.title || (c.sort ? 'Sort' : null), 'data-label': c.label,
    onclick: c.sort ? () => {
      // Numbers start biggest first, names A to Z.
      const first = typeof c.sort(rows[0] || {}) === 'string' ? 1 : -1;
      sorts[el.id] = { label: c.label, dir: col === c ? -st.dir : first };
      redraw();
      // The header was rebuilt; keep keyboard focus where it was.
      [...el.querySelectorAll('th')].find((th) => th.dataset.label === c.label)?.focus();
    } : null,
    onkeydown: c.sort ? (e) => { if (e.key === 'Enter') e.currentTarget.click(); } : null,
  }, c.label, col === c ? (st.dir > 0 ? ' ↑' : ' ↓') : null))));
  const body = h('tbody', null, shown.map((row) => h('tr', {
    class: onClick ? 'click' : null, tabindex: onClick ? 0 : null, onclick: onClick ? () => onClick(row) : null,
    onkeydown: onClick ? (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) onClick(row); } : null,
    title: row.title || null,
  },
    cols.map((c) => h('td', { class: cls(c, c.cls) }, c.get(row))))));
  if (shown.length < list.length) {
    body.append(h('tr', null, h('td', { colspan: cols.length, class: 'more' },
      h('button', { class: 'linkish', onclick: () => {
        el.dataset.all = period;
        redraw();
        if (onClick) el.tBodies[0].rows[shown.length]?.focus();  // the first row that was hidden
      } }, `Show all ${list.length}`))));
  }
  if (!rows.length) body.append(h('tr', null, h('td', { colspan: cols.length, class: 'muted' }, empty)));
  el.replaceChildren(head, body);
}

// Mostly unpriced: the dollar figure covers only part of the calls, so it is a floor.
const isFloor = (row) => row.unpriced_calls > 0 && row.unpriced_calls >= row.calls * 0.1;
const costText = (row) => (!row.cost && row.unpriced_calls ? 'unpriced' : (isFloor(row) ? '≥ ' : '') + fmtUsd(row.cost));
function costCell(row) {
  if (!row.cost && row.unpriced_calls) return h('span', { class: 'muted', title: 'no verified price and the agent logs no cost' }, 'unpriced');
  if (isFloor(row)) {
    return h('span', { title: `${fmtInt(row.unpriced_calls)} of ${count(row.calls, 'call')} unpriced; the value covers the rest` },
      h('span', { class: 'muted' }, '≥ '), fmtUsd(row.cost));
  }
  return fmtUsd(row.cost);
}
const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
// On touch a name inside a session row is too easy to hit when opening the row, so it stays a label
// there (the session drawer still filters by project).
const agentLink = (a) => (CLICK === 'tap' ? h('span', { class: 'tag' }, marked(a))
  : h('button', { class: 'flt tag', title: 'Filter to this agent', onclick: stop(() => setFilter('agent', a)) }, marked(a)));
// Names the collector gives work that isn't inside one repo.
const SPECIAL_PROJECTS = {
  '(projects root)': 'worked in ~/Projects itself; a session that moves into a repo for a quarter of its events counts there',
  '(home)': 'started in the home folder',
  '(no project)': 'the agent logged no working folder',
  scratch: 'sessions in a throwaway folder (/tmp, the Claude app\'s scratch workspaces), not inside any project',
  'agent config': "an agent's own config folder (~/.claude, ~/.codex…)",
};
const projectTitle = (p) => (SPECIAL_PROJECTS[p] ? `${p}: ${SPECIAL_PROJECTS[p]}` : null);
const projectLink = (p) => (CLICK === 'tap' ? h('span', { class: 'tag' }, swatch(projectColor(p)), marked(pname(p)))
  : h('button', { class: 'flt tag', title: projectTitle(p) ? `${projectTitle(p)}. Click to filter.` : 'Filter to this project',
    onclick: stop(() => setFilter('project', p)) }, swatch(projectColor(p)), marked(pname(p))));

function sparkBuckets(values) {
  const per = Math.ceil(values.length / Math.min(values.length, 30));
  const out = [];
  for (let i = 0; i < values.length; i += per) out.push(sum(values.slice(i, i + per)));
  return out;
}
// One scale for every row, so a tall bar means a big day whichever project it is.
function spark(buckets, max, color) {
  const W = 120, H = 20, bw = W / buckets.length;
  return s('svg', { class: 'spark', width: W, height: H, 'aria-hidden': 'true' },
    buckets.map((v, i) => (v ? s('rect', { x: i * bw + 0.5, y: H - Math.max(1.5, (v / max) * H), width: Math.max(1, bw - 1.5),
      height: Math.max(1.5, (v / max) * H), rx: 1, style: `fill:${color}` }) : null)));
}

// Projects as a butterfly: time to the left, money to the right, the name between.
// A short bar on one side and a long one on the other is the story (10 minutes, $22).
const PROJECTS_SHOWN = 8;
let projectsAll = false;
function renderProjects(d) {
  const rows = d.by_project.filter((r) => r.active_min > 0 || r.cost > 0).sort((a, b) => b.active_min - a.active_min || b.cost - a.cost);
  const maxMin = Math.max(1, ...rows.map((r) => r.active_min));
  const maxCost = Math.max(0.01, ...rows.map((r) => r.cost || 0));
  const total = Math.max(1, sum(rows.map((r) => r.active_min)));
  const year = spanDays() > 90;
  const periods = isRange() ? (year ? months(d.days) : d.days) : null;
  const sparks = periods ? Object.fromEntries(rows.map((r) => [r.name, sparkBuckets(periods.map((x) => x.active_by_project[r.name] || 0))])) : null;
  const sparkMax = sparks ? Math.max(1, ...Object.values(sparks).flat()) : 1;
  const shown = projectsAll ? rows : rows.slice(0, PROJECTS_SHOWN);
  const line = (r) => {
    const c = projectColor(r.name);
    const tip = [h('strong', null, pname(r.name)), h('div', { class: 'muted' }, r.name), h('div', null, `${fmtMin(r.active_min)} · ${Math.round((r.active_min / total) * 100)}% of agent time`),
      h('div', null, `${costText(r)} API value · ${count(r.sessions, 'session')}`), h('div', { class: 'muted' }, rowSummary(r)),
      projectTitle(r.name) ? h('div', { class: 'muted' }, SPECIAL_PROJECTS[r.name]) : null];
    return h('button', { class: 'bf-row', onclick: () => setFilter('project', r.name),
      onmouseenter: (e) => showTip(e, tip), onmousemove: moveTip, onmouseleave: hideTip },
    h('span', { class: 'bf-l' }, h('span', { class: 'bf-v' }, fmtMin(r.active_min)),
      h('i', { style: `width:${(r.active_min / maxMin) * 100}%;background:${c}` })),
    h('span', { class: 'bf-n' }, h('span', { class: 'nm' }, marked(pname(r.name))), sparks ? spark(sparks[r.name], sparkMax, c) : null),
    h('span', { class: 'bf-r' }, h('i', { style: `width:${((r.cost || 0) / maxCost) * 100}%;--c:${c}` }),
      h('span', { class: 'bf-v' }, costCell(r))));
  };
  $('projects').replaceChildren(
    h('div', { class: 'bf-head' }, h('span', null, 'agent time'), h('span'), h('span', null, 'API value')),
    ...shown.map(line),
    rows.length > PROJECTS_SHOWN ? h('button', { class: 'more', onclick: () => { projectsAll = !projectsAll; renderProjects(d); } },
      projectsAll ? 'Show fewer' : `Show all ${rows.length}`) : '',
  );
}

const rowSummary = (r) => [`${count(r.prompts, 'prompt')} · ${count(r.calls, 'model call')}`,
  // Not when a tenth or more is unpriced: those hours would dilute the rate (see costCell).
  r.cost > 0 && r.active_min >= 10 && r.unpriced_calls < r.calls * 0.1
    ? `${fmtUsd(r.cost / (r.active_min / 60))} per active hour` : null].filter(Boolean).join(' · ');

// One bar per machine, as long as its agent time, cut into the projects it worked on.
// Agents show as chips under it only when more than one ran.
function renderAgents(d) {
  const machines = d.by_machine.filter((m) => m.active_min > 0).sort((a, b) => b.active_min - a.active_min);
  const maxMin = Math.max(1, ...machines.map((m) => m.active_min));
  const rows = machines.map((m) => {
    const per = {};
    for (const x of d.sessions) if (x.machine === m.name) per[x.project] = (per[x.project] || 0) + (x.active_min || 0);
    const parts = Object.entries(per).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
    const all = Math.max(1, sum(parts.map(([, v]) => v)));
    const top = parts.slice(0, 5);
    const restMin = sum(parts.slice(5).map(([, v]) => v));
    const segs = top.map(([p, v]) => ({ p, v, c: projectColor(p) }));
    if (restMin) segs.push({ p: `${parts.length - 5} more`, v: restMin, c: 'var(--other)' });
    return h('div', { class: 'mc-row' },
      h('button', { class: 'flt mc-name', title: 'Filter to this machine', onclick: () => setFilter('machine', m.name) }, m.name),
      h('div', { class: 'mc-track' }, h('div', { class: 'mc-bar', style: `width:${(m.active_min / maxMin) * 100}%` },
        segs.map((x) => h('i', { style: `flex:${x.v} 1 0;background:${x.c}`,
          onmouseenter: (e) => showTip(e, [h('strong', null, `${m.name} · ${pname(x.p)}`), h('div', null, `${Math.round((x.v / all) * 100)}% of its time`)]),
          onmousemove: moveTip, onmouseleave: hideTip })))),
      h('span', { class: 'mc-v' }, fmtMin(m.active_min), h('b', null, costText(m))));
  });
  const agents = d.by_agent.filter((a) => a.active_min > 0 || a.calls > 0).sort((a, b) => b.active_min - a.active_min);
  $('agents').replaceChildren(...rows,
    agents.length > 1 ? h('div', { class: 'agent-chips' }, agents.map((a) => h('button', { class: 'chip', title: `${rowSummary(a)} · click to filter`,
      onclick: () => setFilter('agent', a.name) }, a.name, h('b', null, fmtMin(a.active_min))))) : '',
    !rows.length ? h('p', { class: 'muted' }, 'Nothing here.') : '');
}

// A ring of model calls (not API value: unpriced models would vanish), the price beside each.
const MODEL_SHADES = ['var(--text)', 'var(--s1)', 'var(--muted)', 'var(--faint)', 'var(--line)'];
function renderModels(rows) {
  rows = rows.slice().sort((a, b) => b.calls - a.calls);
  if (rows.length > 5) {  // five plus the rest, or a year lists forty
    const rest = rows.slice(4);
    const add = (f) => sum(rest.map((r) => r[f] || 0));
    rows = [...rows.slice(0, 4), { name: `${rest.length} other models`, calls: add('calls'), cost: add('cost'), unpriced_calls: add('unpriced_calls'),
      input: add('input'), cache_read: add('cache_read'), cache_write_5m: add('cache_write_5m'), cache_write_1h: add('cache_write_1h'), output: add('output') }];
  }
  const total = Math.max(1, sum(rows.map((r) => r.calls)));
  const prompt = (r) => r.input + r.cache_read + r.cache_write_5m + r.cache_write_1h;
  const R = 46, C = 2 * Math.PI * R;
  let off = 0;
  const arcs = rows.map((r, i) => {
    const len = (r.calls / total) * C;
    const el = s('circle', { cx: 60, cy: 60, r: R, fill: 'none', 'stroke-width': 16, style: `stroke:${MODEL_SHADES[Math.min(i, 4)]}`,
      'stroke-dasharray': `${Math.max(0, len - 1.5)} ${C}`, 'stroke-dashoffset': -off, transform: 'rotate(-90 60 60)' });
    off += len;
    return el;
  });
  const short = (n) => n.replace(/^claude-/, '').replace(/-\d{8}$/, '').replace(/-(\d)-(\d)$/, ' $1.$2').replace(/^./, (c) => c.toUpperCase());
  $('models').replaceChildren(!rows.length ? h('p', { class: 'muted' }, 'Nothing here.') : h('div', { class: 'donut' },
    s('svg', { viewBox: '0 0 120 120', width: 120, height: 120, 'aria-hidden': 'true' }, arcs,
      svgText({ x: 60, y: 58, 'text-anchor': 'middle', class: 'donut-n' }, fmtTok(total).replace('.0k', 'k')),
      svgText({ x: 60, y: 74, 'text-anchor': 'middle', class: 'donut-l' }, 'calls')),
    h('div', { class: 'donut-keys' }, rows.map((r, i) => h('div', { class: 'dk', title: `${r.name} · ${fmtInt(r.calls)} calls · ${prompt(r) ? fmtShare(r.cache_read / prompt(r)) + ' read from cache' : ''}\n`
      + `${fmtTok(r.input)} uncached input · ${fmtTok(r.cache_read)} cache read · ${fmtTok(r.cache_write_5m + r.cache_write_1h)} cache write · ${fmtTok(r.output)} output` },
    h('i', { style: `background:${MODEL_SHADES[Math.min(i, 4)]}` }), h('span', { class: 'n' }, short(r.name)),
    h('span', { class: 'p' }, fmtShare(r.calls / total)), h('span', { class: 'c' }, costCell(r)))))));
}

// Prompt text as shown: the collector's [scheduled] mark (a /loop or cron re-fire, or a script's
// headless `claude -p` run: nobody typed it) becomes a timer.
const shownPrompt = (t) => (t || '').replace(/^\[scheduled\] /, '⏱ ').replace(/^\[mid-run\] /, '↪ ');

// A session nobody typed into: say what it is instead of a bare dash.
const noPrompt = (r) => (r.model === 'codex-auto-review' ? 'Codex reviewing its own actions (automatic)'
  : r.calls ? 'no prompt typed (resumed, scheduled or headless)' : '');

// Text with the (folded) query marked, matching case and accents loosely like the search.
function highlight(text, q) {
  let folded = '';
  const at = [];
  for (let i = 0; i < text.length; i++) { at.push(folded.length); folded += fold(text[i]); }
  const pos = q ? folded.indexOf(q) : -1;
  if (pos < 0) return text;
  const i = at.findIndex((p) => p >= pos);
  let j = at.findIndex((p) => p >= pos + q.length);
  if (j < 0) j = text.length;
  return [text.slice(0, i), h('mark', null, text.slice(i, j)), text.slice(j)];
}
// A search can match a session's agent, machine or project too: mark those where they show.
const marked = (text) => (state.query.length >= 2 ? highlight(text, state.query) : text);
// While searching, a session shows the prompt that matched, not its first one.
function promptShown(r) {
  const q = state.query.length >= 2 ? state.query : '';  // one letter would mark noise everywhere
  const hit = q && state.queryKeys.get(r.key);
  if (hit && !fold(r.first_prompt).includes(q)) return highlight(shownPrompt(hit), q);
  return r.first_prompt ? (q ? highlight(shownPrompt(r.first_prompt), q) : shownPrompt(r.first_prompt)) : null;
}

function renderSessions() {
  const range = isRange();
  const list = range ? state.data.sessions : state.data.sessions.slice().reverse();
  const q = state.query;
  const matches = list.filter((x) => !q || state.queryKeys.has(x.key) || [x.project, x.agent, x.machine, x.model, x.first_prompt]
    .some((v) => fold(v).includes(q)));
  const running = new Map((state.meta.live || []).map((x) => [`${x.machine}|${x.id}`, x.state]));
  // The first 25 (a range: the most expensive; a day: the latest), then "Show all".
  // A batch (the same prompt in the same project, like an eval run) folds into one row with ×N.
  const rows = [];
  const batch = new Map();
  for (const x of matches) {
    const k = (x.title || x.first_prompt) && !q ? `${x.project}\u0000${x.title || x.first_prompt.slice(0, 60)}` : null;
    const g = k && batch.get(k);
    if (!g) { const r = { ...x, n: 1, members: [x] }; rows.push(r); if (k) batch.set(k, r); continue; }
    g.n += 1; g.members.push(x);
    for (const f of ['active_min', 'cost', 'calls', 'unpriced_calls', 'prompts']) g[f] = (g[f] || 0) + (x[f] || 0);
  }
  state.listed = matches;  // what the CSV export writes (every session, unfolded)
  $('sessions-title').textContent = q ? `Matching sessions (${matches.length})` : `Sessions (${list.length})`;
  const order = { latest: (a, b) => (b.began || b.start) - (a.began || a.start), cost: (a, b) => b.cost - a.cost, time: (a, b) => b.active_min - a.active_min };
  const sorted = rows.slice().sort(order[state.sessSort] || (range ? order.cost : order.latest));
  const maxAct = Math.max(1, ...sorted.map((r) => r.active_min || 0));
  const maxCost = Math.max(0.01, ...sorted.map((r) => r.cost || 0));
  const limit = q || state.sessAll ? sorted.length : 8;
  const item = (r) => {
    const c = projectColor(r.project);
    const prompt = promptShown(r);
    return h('button', { class: 'feed-row', onclick: () => openSession(r),
      onmouseenter: (e) => showTip(e, [h('b', null, sessName(r)), h('div', { class: 'muted' }, `${pname(r.project)} · ${r.agent} · ${r.machine}${r.n > 1 ? ` · ${r.n} identical runs` : ''}${r.title_from === 'task' ? ' · from its task' : ''}`),
        h('div', { style: 'margin-top:4px' }, (prompt || noPrompt(r)))]), onmousemove: moveTip, onmouseleave: hideTip },
    h('span', { class: 'av', style: `--c:${c}` }, initials(r.project), running.has(r.key) ? h('i', { class: 'av-live ' + (running.get(r.key) === 'working' ? 'work' : 'wait') }) : null),
    h('span', { class: 'fr-main' }, h('span', { class: 'fr-t' }, marked(sessName(r)), r.n > 1 ? h('span', { class: 'times' }, `×${r.n}`) : null),
      h('span', { class: 'fr-s' }, h('i', { class: 'sw', style: `background:${c}` }), marked(pname(r.project)), ' · ', range ? fmtDateTime(r.start) : fmtTime(r.start), ' · ', MACHINE_ICON[r.machine] || '', ' ', marked(r.machine)),
      q && prompt ? h('span', { class: 'fr-q' }, prompt) : null),
    h('span', { class: 'fr-bar' }, h('i', { style: `width:${Math.max(4, ((r.active_min || 0) / maxAct) * 100)}%;background:${c}` }), h('b', null, fmtMin(r.active_min))),
    h('span', { class: 'fr-cost', style: `--w:${Math.max(6, ((r.cost || 0) / maxCost) * 100)}%` }, costCell(r)));
  };
  const sortBtn = (k, label) => h('button', { class: (state.sessSort || (range ? 'cost' : 'latest')) === k ? 'on' : null, onclick: () => { state.sessSort = k; renderSessions(); } }, label);
  $('sessions').replaceChildren(
    h('div', { class: 'seg feed-sort' }, sortBtn('latest', '🕐 Latest'), sortBtn('cost', '💰 Priciest'), sortBtn('time', '⏱️ Longest')),
    ...sorted.slice(0, limit).map(item),
    !sorted.length ? h('p', { class: 'muted' }, q ? 'No session matches the search.' : 'No sessions.') : '',
    sorted.length > limit ? h('button', { class: 'more', onclick: () => { state.sessAll = true; renderSessions(); } }, 'Browse all') : '',
  );
}

// ------------------------------------------------------------------ session drawer

let drawerSession = null;

let drawerOpener = null;
let drawerTitle = null;  // the list row's name (its task or a model-written one): the session API doesn't carry it
async function openSession(ref) {
  hideTip();
  drawerTitle = ref.title || null;
  if ($('drawer').hidden) drawerOpener = document.activeElement;
  for (const el of document.querySelectorAll('header.top, main')) el.inert = true;
  const body = $('drawer-body');
  body.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
  $('drawer').hidden = false;
  try {
    // a link from Tasks or Decisions knows the session id alone: the hub finds its machine
    drawerSession = await api(`/api/session?${ref.machine ? `machine=${encodeURIComponent(ref.machine)}&` : ''}session=${encodeURIComponent(ref.id)}`);
  } catch (e) {
    body.replaceChildren(h('p', { class: 'error' }, e.message));
    return;
  }
  renderDrawer();
  $('drawer').querySelector('.drawer-close').focus();
  // Opened from a search: bring the first prompt that matched into view.
  $('drawer-body').querySelector('.turn .text.hit')?.closest('.turn').scrollIntoView({ block: 'center' });
}

// How to pick a session back up, for the agents whose CLI can resume by id.
function resumeCommand(full) {
  const id = full.id.slice(full.id.indexOf(':') + 1);
  if (full.agent === 'Claude Code') return `claude --resume ${id}`;
  if (full.agent === 'Codex') return `codex resume ${id}`;
  if (full.agent === 'OpenCode') return `opencode -s ${id}`;
  if (full.agent === 'Grok') return `grok --resume ${id}`;
  return null;
}
function copyRow(cmd, note) {
  // http:// on the tailnet has no async clipboard API; a selected input and execCommand work everywhere.
  const input = h('input', { class: 'cmd', readonly: true, value: cmd, 'aria-label': 'Resume command', onfocus: (e) => e.target.select() });
  const btn = h('button', { class: 'copy', onclick: () => {
    input.focus(); input.select();
    input.setSelectionRange(0, cmd.length);  // iOS ignores select() on a readonly input
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* ignore */ }
    btn.textContent = ok ? 'Copied' : CLICK === 'tap' ? 'Selected' : 'Press ⌘C';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
  } }, 'Copy');
  return h('div', { class: 'resume' }, input, btn, h('span', { class: 'muted' }, note));
}

// A model id as people say it: "claude-haiku-4-5-20251001" → "Haiku 4.5".
const modelName = (n) => n.replace(/^claude-/, '').replace(/-\d{8}$/, '').replace(/-(\d)-(\d)$/, ' $1.$2').replace(/^./, (c) => c.toUpperCase());

function renderDrawer() {
  const full = drawerSession;
  const span = Math.max(60, full.end - full.start);
  const pct = (ts) => ((ts - full.start) / span) * 100;
  const prompts = full.turns.filter((t) => t.text != null);
  const SCHED = '[scheduled] ';  // the collector's mark on a turn nobody typed (/loop, cron, script)
  const scheduled = prompts.filter((t) => t.text.startsWith(SCHED)).length;
  const maxCost = Math.max(0.0001, ...full.turns.map((t) => t.cost));
  // With nothing priced every turn is $0, so there is no order by cost to offer.
  const byCost = full.cost > 0 && full.turns.length > 1;
  const MID = '[mid-run] ';  // typed while the agent was still working
  const body = (t) => (t.text.startsWith(SCHED) ? t.text.slice(SCHED.length) : t.text.startsWith(MID) ? t.text.slice(MID.length) : t.text);
  // The search's matches show open and marked.
  const hit = (t) => state.query.length >= 2 && fold(body(t)).includes(state.query);
  const turns = byCost && state.turnsSort === 'cost' ? full.turns.slice().sort((a, b) => b.cost - a.cost) : full.turns;
  const color = projectColor(full.project);
  const sameDay = localDate(full.start) === localDate(full.end);
  const models = Object.entries(full.models).sort((a, b) => b[1] - a[1]);
  const worked = Object.entries(full.projects || {}).sort((a, b) => b[1] - a[1]);
  const typed = prompts.length - scheduled;
  // One plain sentence for what the agent did; the token counts wait under "Details".
  const did = [
    full.calls ? count(full.calls, 'model call') : 'no model calls',
    full.tools ? count(full.tools, 'tool step') : null,
  ].filter(Boolean).join(' and ') + (models.length ? ` on ${models.slice(0, 2).map(([m]) => modelName(m)).join(' and ')}` : '')
    + (full.subagent_calls ? `, ${fmtInt(full.subagent_calls)} by helper agents` : '') + '.';
  const stat = (value, label) => h('div', null, h('b', null, value), h('span', null, label));
  const row = (label, value) => h('div', null, h('dt', null, label), h('dd', null, value));
  // The strip earns its place only when there is a shape to see: a long session or breaks in it.
  const showStrip = span >= 600 || full.bursts.length > 1;

  $('drawer-body').replaceChildren(...[
    h('div', { class: 'sd-eyebrow' },
      h('button', { class: 'linkish', title: projectTitle(full.project) || 'Filter to this project',
        onclick: () => { closeDrawer(); setFilter('project', full.project); } }, swatch(color), ' ', pname(full.project)),
      h('span', null, `${full.agent} · ${MACHINE_ICON[full.machine] || ''} ${full.machine}`)),
    h('h3', null, sessName({ ...full, title: full.title || drawerTitle, first_prompt: prompts[0]?.text })),
    h('div', { class: 'sd-when' },
      h('button', { class: 'linkish', title: 'Open this day', onclick: () => { closeDrawer(); go({ view: 'day', date: localDate(full.start) }); } },
        sameDay ? `${prettyDate(localDate(full.start))} · ${fmtTime(full.start)}–${fmtTime(full.end)}` : `${fmtDateTime(full.start)} – ${fmtDateTime(full.end)}`)),
    h('div', { class: 'sd-stats' },
      stat(costText(full), 'API value'),
      stat(fmtMin(full.active_min), 'agent time'),
      !typed && scheduled ? stat(fmtInt(scheduled), scheduled === 1 ? 'automated run' : 'automated runs') : stat(fmtInt(typed), typed === 1 ? 'prompt' : 'prompts')),
    h('p', { class: 'sd-did' }, did, scheduled && typed ? ` ${count(scheduled, 'more run')} started by a schedule or script.` : !typed && scheduled ? ' Started by a schedule or script; nobody typed it.' : ''),
    linkedWork(full.id),
    showStrip ? h('h4', null, 'When it worked') : null,
    showStrip ? h('div', { class: 'strip' },
      full.bursts.map(([a, b]) => h('div', { class: 'b', style: `left:${pct(a)}%;width:${Math.max(0.5, pct(b) - pct(a))}%;background:${color}` })),
      prompts.map((t) => h('div', { class: 'tick', style: `left:${pct(t.ts)}%`, title: `${fmtTime(t.ts)} ${body(t).slice(0, 80)}` }))) : null,
    showStrip ? h('div', { class: 'strip-labels' }, h('span', null, fmtTime(full.start)), h('span', null, h('i', { class: 'tick-key' }), ' a prompt'), h('span', null, fmtTime(full.end))) : null,
    h('h4', null, prompts.length ? `What was asked` : 'Model calls',
      byCost ? h('div', { class: 'seg' },
        h('button', { class: state.turnsSort === 'time' ? 'on' : null, 'aria-pressed': String(state.turnsSort === 'time'), onclick: () => { state.turnsSort = 'time'; renderDrawer(); } }, 'In order'),
        h('button', { class: state.turnsSort === 'cost' ? 'on' : null, 'aria-pressed': String(state.turnsSort === 'cost'), onclick: () => { state.turnsSort = 'cost'; renderDrawer(); } }, 'Priciest')) : null),
    h('ol', { class: 'turns' }, turns.map((t) => h('li', { class: 'turn' },
      h('header', null,
        h('time', null, sameDay ? fmtTime(t.ts) : fmtDateTime(t.ts)),
        t.last > t.ts ? h('span', { class: 'ran' }, `ran ${fmtDur(t.last - t.ts)}`) : null,
        t.text?.startsWith(SCHED) ? h('span', { class: 'sched', title: 'a /loop or cron re-fire, or a script running claude -p; nobody typed it' }, 'automated') : null,
        t.text?.startsWith(MID) ? h('span', { class: 'sched', title: 'typed while the agent was still working' }, 'mid-run') : null,
        h('span', { class: 'cost' }, t.unpriced_calls && !t.cost ? (t.calls ? 'unpriced' : '') : costText(t))),
      t.text == null
        ? h('div', { class: 'text none' }, 'Calls before the first prompt (resumed or headless run)')
        : h('div', { class: hit(t) ? 'text open hit' : 'text', title: 'Click to expand', onclick: (e) => {
          const open = e.currentTarget.classList.toggle('open');
          const more = e.currentTarget.nextElementSibling;
          if (!more.hidden) more.textContent = open ? 'Show less' : 'Show more';
        } }, hit(t) ? highlight(body(t), state.query) : body(t)),
      t.text != null ? h('button', { class: 'more', hidden: true, onclick: (e) => {
        const text = e.currentTarget.previousElementSibling;
        e.currentTarget.textContent = text.classList.toggle('open') ? 'Show less' : 'Show more';
      } }, 'Show more') : null,
      h('div', { class: 'stats' }, [
        t.calls ? count(t.calls, 'call') : 'no model calls',
        t.tools ? count(t.tools, 'tool step') : null,
        t.subagent_calls ? `${fmtInt(t.subagent_calls)} by helpers` : null,
      ].filter(Boolean).join(' · ')),
      byCost && t.cost ? h('div', { class: 'meter' }, h('i', { style: `width:${(t.cost / maxCost) * 100}%` })) : null))),
    h('details', { class: 'sd-more' },
      h('summary', null, 'Details'),
      resumeCommand(full) ? copyRow(resumeCommand(full), `Pick it up again: run on ${full.machine}${full.agent === 'Codex' ? '' : ', in the project folder'}`) : null,
      h('dl', null,
        row('Models', models.map(([m, n]) => `${modelName(m)} · ${count(n, 'call')}`).join(', ') || '—'),
        worked.length > 1 ? row('Worked in', worked.map(([p, n]) => `${pname(p)} ${Math.round((n / sum(worked.map((w) => w[1]))) * 100)}%`).join(', ')) : null,
        row('Tokens in · out', `${fmtTok(full.input)} · ${fmtTok(full.output)}`),
        row('Cache read · written', `${fmtTok(full.cache_read)} · ${fmtTok(full.cache_write_5m + full.cache_write_1h)}`),
        row('Started · ended', `${fmtDateTime(full.start)} · ${fmtDateTime(full.end)}`)),
      h('a', { class: 'sd-tasks', href: `/tasks#/backlog/${encodeURIComponent(full.project)}` }, `All ${pname(full.project)} tasks →`)),
  ].filter(Boolean));
  for (const text of $('drawer-body').querySelectorAll('.turn .text:not(.none)')) {
    if (text.scrollHeight > text.clientHeight + 2) text.nextElementSibling.hidden = false;
  }
}
// The tasks this session took or filed and the decisions it asked for, each a link to its page.
function linkedWork(id) {
  const box = h('div', { class: 'linked' });
  const q = `status=all&limit=50&session=${encodeURIComponent(id)}`;
  Promise.all([api(`/api/tasks?${q}`).catch(() => null), api(`/api/questions?${q}`).catch(() => null)]).then(([t, d]) => {
    if (drawerSession?.id !== id) return;
    const tasks = t?.tasks || [], decisions = d?.questions || [];
    box.replaceChildren(...[  // replaceChildren would print a null as the text "null"
      tasks.length ? h('h4', null, `Tasks (${tasks.length})`) : null,
      tasks.length ? h('ul', { class: 'linked-list' }, tasks.map((x) => h('li', null,
        h('a', { href: `/tasks#/task/${x.id}` }, x.title), h('span', { class: 'muted' }, ` · ${x.status}`)))) : null,
      decisions.length ? h('h4', null, `Decisions (${decisions.length})`) : null,
      decisions.length ? h('ul', { class: 'linked-list' }, decisions.map((x) => h('li', null,
        h('a', { href: `/decisions#/q/${x.id}` }, x.question),
        h('span', { class: 'muted' }, x.status === 'open' ? ' · waiting for you' : x.answer ? ` · ${x.answer}` : ` · ${x.status}`)))) : null,
    ].filter(Boolean));
  });
  return box;
}

function closeDrawer() {
  if ($('drawer').hidden) return;
  $('drawer').hidden = true;
  drawerSession = null;
  for (const el of document.querySelectorAll('header.top, main')) el.inert = false;
  if (drawerOpener?.isConnected) drawerOpener.focus();
  drawerOpener = null;
}

// ------------------------------------------------------------------ wiring

function step(dir) {
  const n = addDays(state.date, dir * spanDays());
  go({ date: n > state.meta.today ? state.meta.today : n });
}
function segToggle(id, key, pref = key) {
  $(id).addEventListener('click', (e) => {
    const v = e.target.closest('button')?.dataset.v;
    if (!v || state[key] === v) return;
    state[key] = v;
    prefs.set(pref, v);
    render();
  });
}

$('view-tabs').addEventListener('click', (e) => {
  const v = e.target.closest('button')?.dataset.view;
  if (v && v !== state.view) go({ view: v });
});
$('prev').onclick = () => step(-1);
$('next').onclick = () => step(1);
$('today').onclick = () => go({ date: state.meta.today });
// The date opens the browser's date picker: jump anywhere without stepping day by day.
$('date-label').onclick = () => {
  const input = $('date-input');
  input.max = state.meta.today;
  input.min = state.meta.first_date || '';
  input.value = state.date;
  try { input.showPicker(); } catch { input.focus(); }
};
$('date-input').addEventListener('change', (e) => {
  if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)) go({ date: e.target.value });
});
segToggle('lanes-toggle', 'lanes', 'lanes2');
segToggle('trend-toggle', 'trend');
segToggle('trend-by', 'trendBy');
// Matches on the listed fields show at once; the hub then adds sessions whose later prompts match.
let searchTimer, searchSeq = 0;
function runSearch() {
  const q = state.query;
  state.queryKeys = new Map();
  renderSessions();
  clearTimeout(searchTimer);
  if (q.length < 2 || !state.data) return;
  const my = ++searchSeq;
  const to = state.date, from = addDays(state.date, 1 - spanDays());
  searchTimer = setTimeout(() => api(`/api/search?q=${encodeURIComponent(q)}&from=${from}&to=${to}`).then((keys) => {
    if (my !== searchSeq || q !== state.query) return;
    state.queryKeys = new Map(keys);  // key -> the matching prompt, around the match
    renderSessions();
  }).catch(() => {}), 200);
}
// A CSV download named for the period and filters. The BOM makes Excel read it as UTF-8,
// so Czech accents survive.
function downloadCsv(kind, head, rows) {
  const cell = (v) => (v == null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const csv = '\ufeff' + [head, ...rows].map((r) => r.map(cell).join(',')).join('\n') + '\n';
  const from = addDays(state.date, 1 - spanDays());
  const tag = Object.values(state.filter).filter(Boolean).map((v) => v.replace(/[^\w.-]+/g, '-')).join('-');
  const a = h('a', { href: URL.createObjectURL(new Blob([csv], { type: 'text/csv' })),
    download: `agent-usage-${kind}_${from === state.date ? from : `${from}_${state.date}`}${tag ? '_' + tag : ''}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
// The sessions in the list (filters and search applied), one row per session.
$('export').addEventListener('click', () => downloadCsv('sessions',
  ['date', 'start', 'agent', 'machine', 'project', 'model', 'active_min', 'prompts', 'model_calls', 'api_value_usd', 'unpriced_calls', 'first_prompt'],
  (state.listed || []).slice().sort((a, b) => a.start - b.start).map((r) => [localDate(r.start), fmtTime(r.start), r.agent, r.machine,
    r.project, r.model, r.active_min, r.prompts, r.calls, r.cost.toFixed(2), r.unpriced_calls, (r.first_prompt || '').replace(/\s+/g, ' ')])));
// Hours per project per day, split at midnight like the views: a client timesheet.
$('export-projects').addEventListener('click', () => {
  const d = state.data;
  const rows = isRange()
    ? d.days.flatMap((x) => Object.keys({ ...x.active_by_project, ...x.cost_by_project }).sort()
      .filter((p) => x.active_by_project[p] || x.cost_by_project[p])
      .map((p) => [x.date, p, x.active_by_project[p] || 0, (x.cost_by_project[p] || 0).toFixed(2)]))
    : d.by_project.map((p) => [state.date, p.name, p.active_min, p.cost.toFixed(2)]);
  downloadCsv('projects', ['date', 'project', 'active_min', 'api_value_usd'], rows);
});
$('search').addEventListener('input', (e) => { state.query = fold(e.target.value.trim()); runSearch(); });
$('drawer').addEventListener('click', (e) => { if (e.target.closest('[data-close]')) closeDrawer(); });
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.matches('input, select, textarea')) {
    if (e.key === 'Escape' && e.target.id === 'search' && e.target.value) {
      e.target.value = '';
      state.query = '';
      runSearch();
    } else if (e.key === 'Escape') e.target.blur();
    return;
  }
  if (e.key === 'Escape') return closeDrawer();
  if (!$('drawer').hidden || !state.meta) return;
  const views = { 1: 'day', 2: '7', 3: '30', 4: '365' };
  if (e.key === 'ArrowLeft' && !$('prev').disabled) step(-1);
  else if (e.key === 'ArrowRight' && state.date < state.meta.today) step(1);
  else if (e.key === 't' || e.key === 'T') go({ date: state.meta.today });
  else if (views[e.key]) go({ view: views[e.key] });
  else if (e.key === '/') { e.preventDefault(); $('search').focus(); }
  else return;
  e.preventDefault();
});
window.addEventListener('hashchange', () => { readHash(); load(); });
let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  // Charts size to their container: redraw every view, the day's pace chart included.
  resizeTimer = setTimeout(() => { if (state.data) render(); }, 150);
});

if (CLICK === 'tap') $('search').placeholder = $('search').placeholder.replace(/\s+\/$/, '');  // no keyboard, no shortcut
document.querySelectorAll('.note.inline').forEach((n) => { n.textContent = n.textContent.replace('click', CLICK); });
readHash();
// Today's view paints from the last visit at once and refreshes behind it, instead of a blank page for a second.
const usageSnap = {
  get() { try { return JSON.parse(localStorage.getItem('hub.snap.usage')); } catch { return null; } },
  set(v) {  // full storage: the biggest other page snapshot goes first (same as snapPut in shared.js)
    const text = JSON.stringify(v);
    for (let i = 0; i < 4; i++) {
      try { localStorage.setItem('hub.snap.usage', text); return; } catch { /* full, or blocked */ }
      try {
        const others = Object.keys(localStorage).filter((k) => k.startsWith('hub.snap.') && k !== 'hub.snap.usage');
        const big = others.sort((a, b) => localStorage.getItem(b).length - localStorage.getItem(a).length)[0];
        if (!big) return;
        localStorage.removeItem('hub.snap.usage'); localStorage.removeItem(big);
      } catch { return; }
    }
  },
};
(function paintSnapshot() {
  const last = usageSnap.get();
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Prague' }).format(new Date());
  if (!last?.meta || !last.data || last.data.date !== today || state.view !== 'day' || filterQuery()) return;
  if (state.date && state.date !== today) return;
  state.meta = last.meta; TZ = last.meta.tz || TZ; state.data = last.data; state.date = today;
  try {
    state.colors = assignColors(last.data);
    renderHeader(); renderLive(); renderFilters(); render();
    $('main').classList.remove('booting');
  } catch { state.data = null; }  // an old snapshot shape: wait for the hub instead
})();
load({ quiet: true, follow: !state.date || state.date === state.meta?.today });
// Keep a view that includes today current, and the live strip fresh everywhere.
function refresh() {
  if (!$('drawer').hidden || document.hidden) return;
  // After a failed load, retry it: the page still shows the view before the one asked for,
  // or nothing at all when the very first load failed (no meta yet).
  if (!$('error').hidden) return load({ quiet: true });
  if (!state.meta) return;
  // Following today: after midnight, move on to the new day instead of staying on yesterday.
  if (state.date === state.meta.today) load({ quiet: true, follow: true });
  else api('/api/meta').then((m) => { state.meta = m; renderLive(); renderHeader(); }).catch(() => {});
}
setInterval(refresh, 60000);
// A new version deployed: reload, so an open tab never shows yesterday's page.
let appTag = null;
setInterval(() => {
  if (document.hidden || !$('drawer').hidden) return;
  fetch('app.js', { method: 'HEAD', cache: 'no-store' }).then((r) => {
    const tag = r.headers.get('ETag');
    if (appTag && tag && tag !== appTag) location.reload();
    appTag = tag || appTag;
  }).catch(() => {});
}, 60000);
// A background tab skips refreshes; catch up the moment it is looked at again.
let hiddenAt = 0;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) hiddenAt = Date.now();
  else if (Date.now() - hiddenAt > 30000) refresh();
});
