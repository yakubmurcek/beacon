// Usage page state: the view, date and filters, kept in the URL hash, and the data loads.
import { $, api, snap } from './shared.js';
import { openSession } from './usage-drawer.js';
import { renderFilters, renderHeader, renderLive } from './usage-header.js';
import { render } from './usage-render.js';
import { renderSessions } from './usage-tables.js';
import { hideTip } from './usage-timeline.js';
import { addDays, assignColors, prefs, setTZ } from './usage-util.js';

const VIEWS = ['day', '7', '30', '365'];
export const FILTER_KEYS = ['agent', 'machine', 'project'];
export const state = {
  view: 'day', date: null, filter: { agent: null, machine: null, project: null },
  lanes: prefs.get('lanes2', 'project'), trend: prefs.get('trend', 'cost'), trendBy: prefs.get('trendBy', 'project'),
  turnsSort: 'time', zoom: null, sessSort: null, sessAll: false, meta: null, data: null, query: '', queryKeys: new Map(), colors: {},
};
export const isRange = () => state.view !== 'day';
export const spanDays = () => (state.view === 'day' ? 1 : Number(state.view));

// A session's own address, so Tasks and Decisions can link straight to it: #/session/<id>.
// It opens today's view with that session's drawer on top.
function sessionFromHash() {
  const m = location.hash.match(/^#\/session\/(.+)$/);
  if (!m) return false;
  state.pendingSession = decodeURIComponent(m[1]);
  history.replaceState(null, '', '#/day');
  return true;
}

export function readHash() {
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
export function filterQuery() {
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
export async function load({ push = false, quiet = false, follow = false, top = false } = {}) {
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
    if (days === 1 && !q && data.date === meta.today) snap.set('usage', { meta, data });
    state.meta = meta;
    setTZ(meta.tz);
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

export function go(changes, opts) {
  if ('date' in changes || 'view' in changes) { state.zoom = null; state.sessAll = false; state.sessSort = null; }
  const top = 'view' in changes && changes.view !== state.view;
  Object.assign(state, changes);
  if (state.meta && state.date > state.meta.today) state.date = state.meta.today;
  load({ push: true, top, ...opts });
}
export function setFilter(key, value) {
  state.filter = { ...state.filter, [key]: state.filter[key] === value ? null : value };
  hideTip();
  load({ push: true });
}

// Matches on the listed fields show at once; the hub then adds sessions whose later prompts match.
let searchTimer, searchSeq = 0;
export function runSearch() {
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
