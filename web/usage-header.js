// Usage page header: the live strip of running sessions and the filter chips.
import { $, h } from './shared.js';
import { initials, pname } from './names.js';
import { openSession } from './usage-drawer.js';
import { FILTER_KEYS, load, setFilter, spanDays, state } from './usage-state.js';
import { costText, shownPrompt } from './usage-tables.js';
import { hideTip, moveTip, showTip } from './usage-timeline.js';
import { addDays, fmtAgo, fmtDateTime, prettyDate, projectColor, shortDate } from './usage-util.js';

// The chosen button of a segmented control: shown by .on, announced by aria-pressed.
export function markOn(id, isOn) {
  for (const b of $(id).querySelectorAll('button')) {
    b.classList.toggle('on', isOn(b));
    b.setAttribute('aria-pressed', String(isOn(b)));
  }
}

export function renderHeader() {
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
  /** @type {HTMLButtonElement} */ ($('next')).disabled = state.date >= state.meta.today;
  // Nothing older than the first tracked day exists; stepping further back only shows empties.
  /** @type {HTMLButtonElement} */ ($('prev')).disabled = !!state.meta.first_date && from <= state.meta.first_date;
  /** @type {HTMLButtonElement} */ ($('today')).disabled = state.date === state.meta.today;

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
export const MACHINE_ICON = window.BEACON.machine_icons;  // settings `machines`, through /config.js
let liveAll = false;
export function renderLive() {
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
export function renderFilters() {
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
