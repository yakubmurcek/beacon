// Usage page day timeline: every session as a lane over the day, and the chart tooltip.
import { $, h, s } from './shared.js';
import { pname } from './names.js';
import { openSession } from './usage-drawer.js';
import { markOn } from './usage-header.js';
import { setFilter, state } from './usage-state.js';
import { costText, projectTitle, shownPrompt } from './usage-tables.js';
import { CLICK, count, fmtMin, fmtTime, fmtUsd, fmtUsdAxis, niceMax, prettyDate, projectColor, sessName, shortDate, sum, svgText, swatch } from './usage-util.js';

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
export function renderTimeline(d) {
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
export const FLAT = { ArrowLeft: -1, ArrowRight: 1, Home: 0, End: 0 };
export function roving(root, sel, keys, start = 0) {
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

export function showTip(e, nodes) {
  const tip = $('tooltip');
  tip.replaceChildren(...nodes.filter(Boolean));
  tip.hidden = false;
  moveTip(e);
}
export function moveTip(e) {
  const tip = $('tooltip');
  const x = e.clientX ?? e.target.getBoundingClientRect().right;
  const y = e.clientY ?? e.target.getBoundingClientRect().bottom;
  tip.style.left = Math.max(8, Math.min(window.innerWidth - tip.offsetWidth - 8, x + 14)) + 'px';
  tip.style.top = Math.max(8, Math.min(window.innerHeight - tip.offsetHeight - 8, y + 14)) + 'px';
}
export function hideTip() { $('tooltip').hidden = true; }
export const tipRow = (color, value, label) => h('div', { class: 'row' },
  h('i', { class: 'k', style: `background:${color}` }), h('strong', null, value), h('span', null, label));
