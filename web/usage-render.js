// Usage page render: the totals and the order the sections draw in; where the time went.
import { $, h, s } from './shared.js';
import { initials, pname } from './names.js';
import { layerTotal, months, renderCalendar, renderHours, renderTrend } from './usage-charts.js';
import { isRange, setFilter, spanDays, state } from './usage-state.js';
import { isFloor, renderAgents, renderModels, renderProjects, renderSessions } from './usage-tables.js';
import { hideTip, moveTip, renderTimeline, showTip, tipRow } from './usage-timeline.js';
import { count, fmtInt, fmtMin, fmtShare, fmtTime, fmtTok, fmtUsd, projectColor, sessName, shortDate, sum } from './usage-util.js';

export function render() {
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
