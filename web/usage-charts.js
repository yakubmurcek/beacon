// Usage page charts: per-day bars (7 / 30 days), the year calendar and the weekday × hour heat map.
import { $, h, s } from './shared.js';
import { pname } from './names.js';
import { markOn } from './usage-header.js';
import { go, spanDays, state } from './usage-state.js';
import { costText } from './usage-tables.js';
import { FLAT, hideTip, moveTip, roving, showTip, tipRow } from './usage-timeline.js';
import { cap, CLICK, count, fmtMin, fmtShare, fmtTok, fmtUsd, fmtUsdAxis, niceHours, niceMax, prettyDate, projectColor, shortDate, slotColor, sum, svgText, weekday, WEEKDAY_NAMES, WEEKDAYS } from './usage-util.js';

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
export const layerTotal = (x) => sum(LAYERS.map((l) => x[l.key] || 0));
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
  /** @type {{label: string, color: string, values: number[], detail?: (i: number) => string}[]} */
  const series = colored.map((p) => ({ label: pname(p), color: projectColor(p), values: buckets.map((x) => x[key][p] || 0) }));
  const rest = buckets.map((x) => Object.entries(x[key]).filter(([p]) => !state.colors[p] && x[key][p] > 0).sort((a, b) => b[1] - a[1]));
  const other = rest.map((r) => sum(r.map(([, v]) => v)));
  // The tooltip names what is inside the gray, which in a long view is often most of it.
  const detail = (i) => (rest[i].length ? rest[i].slice(0, 3).map(([p]) => p).join(', ') + (rest[i].length > 3 ? ` +${rest[i].length - 3}` : '') : '');
  if (other.some(Boolean)) series.push({ label: 'Other & unnamed', color: 'var(--other)', values: other, detail });
  return { series, ...fmts };
}

// The year's days summed per calendar month, in the shape stackedColumns reads.
export function months(days) {
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

export function renderTrend(d) {
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

export function renderCalendar(d) {
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

export function renderHours(d) {
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
