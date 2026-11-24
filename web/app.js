// Usage (http://beacon/usage): what the agents cost and where their time went. This file wires the page;
// the parts live in usage-*.js.
import { $, api, h, snap } from './shared.js';
import { closeDrawer } from './usage-drawer.js';
import { renderFilters, renderHeader, renderLive } from './usage-header.js';
import { render } from './usage-render.js';
import { filterQuery, go, isRange, load, readHash, runSearch, spanDays, state } from './usage-state.js';
import { addDays, assignColors, CLICK, fmtTime, fold, localDate, prefs, setTZ, TZ } from './usage-util.js';

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
// Today's view paints from the last visit at once and refreshes behind it (snap, shared.js).
(function paintSnapshot() {
  const last = snap.get('usage');
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date());
  if (!last?.meta || !last.data || last.data.date !== today || state.view !== 'day' || filterQuery()) return;
  if (state.date && state.date !== today) return;
  state.meta = last.meta; setTZ(last.meta.tz); state.data = last.data; state.date = today;
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
