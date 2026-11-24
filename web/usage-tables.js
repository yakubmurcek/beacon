// Usage page tables: projects, models, machines and sessions.
import { $, h, s } from './shared.js';
import { initials, pname } from './names.js';
import { months } from './usage-charts.js';
import { openSession } from './usage-drawer.js';
import { MACHINE_ICON } from './usage-header.js';
import { isRange, setFilter, spanDays, state } from './usage-state.js';
import { hideTip, moveTip, showTip } from './usage-timeline.js';
import { CLICK, count, fmtDateTime, fmtInt, fmtMin, fmtShare, fmtTime, fmtTok, fmtUsd, fold, projectColor, sessName, sum, svgText, swatch } from './usage-util.js';

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
export const isFloor = (row) => row.unpriced_calls > 0 && row.unpriced_calls >= row.calls * 0.1;
export const costText = (row) => (!row.cost && row.unpriced_calls ? 'unpriced' : (isFloor(row) ? '≥ ' : '') + fmtUsd(row.cost));
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
export const projectTitle = (p) => (SPECIAL_PROJECTS[p] ? `${p}: ${SPECIAL_PROJECTS[p]}` : null);
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
export function renderProjects(d) {
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
export function renderAgents(d) {
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
export function renderModels(rows) {
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
export const shownPrompt = (t) => (t || '').replace(/^\[scheduled\] /, '⏱ ').replace(/^\[mid-run\] /, '↪ ');

// A session nobody typed into: say what it is instead of a bare dash.
const noPrompt = (r) => (r.model === 'codex-auto-review' ? 'Codex reviewing its own actions (automatic)'
  : r.calls ? 'no prompt typed (resumed, scheduled or headless)' : '');

// Text with the (folded) query marked, matching case and accents loosely like the search.
export function highlight(text, q) {
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

export function renderSessions() {
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
