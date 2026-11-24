// Build log: every game's sessions as a day-by-day story, with what the screen looked like, what changed,
// how long it took and what it cost. Open one session for its full record: each thing he asked, the
// screenshots of that turn and Claude's answer. Reads /api/buildlog* (hub/buildlog.py).
// Routes: #/<game>   #/<game>/s/<machine>/<session id>
import { $, api, h, markdown, pragueToday, showError, snap } from './shared.js';

const DAYS_PAGE = 10;
let games = snap.get('buildlog.games');
let game = null;       // {game, days} of the open game
let shownDays = DAYS_PAGE;
const fmtDate = new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
const fmtTime = new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { hour: '2-digit', minute: '2-digit', timeZone: window.BEACON.timezone });
const fmtDay = new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: window.BEACON.timezone });

const img = (path, w) => `/buildlog/img/${path.split('/').map(encodeURIComponent).join('/')}?w=${w}`;
function hours(min) {
  if (min < 60) return `${Math.round(min)} min`;
  const h = min / 60;
  return `${h < 10 ? h.toFixed(1).replace(/\.0$/, '') : Math.round(h)} h`;
}
function money(x) {
  if (x >= 1000) return `$${(x / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  if (x >= 10) return `$${Math.round(x)}`;
  return `$${x.toFixed(x < 1 ? 2 : 1)}`;
}
const dayLabel = (d) => d === pragueToday() ? 'Today' : d === pragueToday(-1) ? 'Yesterday' : fmtDate.format(new Date(d + 'T12:00:00Z'));
// replaceChildren with h()'s rules: arrays flatten, null and false leave nothing (not the word "null")
const put = (el, ...kids) => el.replaceChildren(...kids.flat(2).filter((k) => k != null && k !== false));
const plain = (s) => String(s || '').replace(/\*\*|`/g, '').trim();

function route() {
  const [, key, kind, machine, id] = location.hash.split('/').map(decodeURIComponent);
  return { key: key || null, session: kind === 's' ? { machine, id } : null };
}

// ------------------------------------------------------------------ games

function picker() {
  const r = route();
  return h('nav', { class: 'games', 'aria-label': 'Games' }, (games || []).map((g) =>
    h('a', { class: 'game' + (g.key === r.key ? ' on' : ''), href: `#/${g.key}` },
      h('div', { class: 'cover' }, g.cover ? h('img', { src: img(g.cover, 480), alt: '', loading: 'lazy' }) : h('span', null, '🎮')),
      h('div', { class: 'g-text' },
        h('b', null, g.name),
        h('span', null, `${g.days} days · ${hours(g.active_min)} · ${money(g.cost)}`),
        h('span', { class: 'when' }, g.last === pragueToday() ? 'worked on today' : `last ${dayLabel(g.last)}`)))));
}

// ------------------------------------------------------------------ one game

function tiles(g) {
  const t = (v, k, hint) => h('div', { class: 'tile', title: hint }, h('div', { class: 'v' }, v), h('div', { class: 'k' }, k));
  return h('section', { class: 'tiles' },
    t(String(g.days), 'days worked', 'Days with at least one session on this game'),
    t(hours(g.active_min), 'agent time', 'Time Claude was working on it (active minutes, all sessions)'),
    t(money(g.cost), 'API value', 'What the sessions would cost at API list prices (he pays a flat plan)'),
    t(String(g.shots), 'screenshots', 'Screens Claude captured while building and testing'),
    t(String(g.commits), 'commits', 'Changes saved to the game\'s code on GitHub in this period'));
}

function strip(days) {
  // One bar per calendar day from the first session to today: height = agent time that day.
  if (!days.length) return null;
  const by = Object.fromEntries(days.map((d) => [d.date, d]));
  const first = days[days.length - 1].date, last = pragueToday();
  const all = [];
  for (let d = new Date(first + 'T12:00:00Z'); d.toISOString().slice(0, 10) <= last; d.setUTCDate(d.getUTCDate() + 1)) all.push(d.toISOString().slice(0, 10));
  const max = Math.max(...days.map((d) => d.active_min), 1);
  return h('section', { class: 'panel strip-panel' },
    h('div', { class: 'row' }, h('h2', null, 'Every day'),
      h('span', { class: 'legend' }, h('span', null, h('i', { class: 'bar-key' }), 'agent time that day'),
        h('span', null, h('i', { class: 'dot-key' }), 'code changed'))),
    h('div', { class: 'daystrip', role: 'img', 'aria-label': `${days.length} days worked between ${first} and ${last}` },
      all.map((ds) => {
        const d = by[ds];
        const pct = d ? Math.max(6, Math.round(100 * d.active_min / max)) : 0;
        return h('button', {
          class: 'day-bar' + (d ? '' : ' off'), title: d ? `${dayLabel(ds)}: ${hours(d.active_min)}, ${money(d.cost)}${d.changes.length ? `, ${d.changes.length} commits` : ''}` : `${dayLabel(ds)}: nothing`,
          disabled: !d, onclick: () => jump(ds),
        }, h('span', { style: `height:${pct}%` }), d && d.changes.length ? h('i') : null);
      })),
    h('div', { class: 'strip-ends' }, h('span', null, dayLabel(first)), h('span', null, 'Today')));
}

function jump(ds) {
  const i = game.days.findIndex((d) => d.date === ds);
  if (i >= shownDays) { shownDays = i + 3; renderGame(); }
  const el = document.getElementById('day-' + ds);
  if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1400); }
}

function sessionRow(s, key) {
  const pics = (s.shots && s.shots.pick) || [];
  const href = `#/${key}/s/${encodeURIComponent(s.machine)}/${encodeURIComponent(s.id)}`;
  return h('a', { class: 'sess', href },
    pics.length ? h('div', { class: 'thumbs' }, pics.slice(-4).map((p) => h('img', { src: img(p, 240), alt: '', loading: 'lazy' })),
      s.shots.n > 4 ? h('span', { class: 'more-pics' }, `+${s.shots.n - 4}`) : null) : null,
    h('div', { class: 's-text' },
      h('b', null, s.title || plain(s.first_prompt).slice(0, 80) || 'Session'),
      s.did ? h('p', { class: 'did' }, plain(s.did)) : null,
      h('span', { class: 'meta' }, `${fmtTime.format(new Date(s.start * 1000))} · ${hours(s.active_min)} · ${money(s.cost)} · ${s.prompts ? `${s.prompts} ask${s.prompts === 1 ? '' : 's'}` : 'ran on its own'}`)));
}

function dayCard(d, key) {
  const commits = d.changes;
  return h('article', { class: 'day', id: 'day-' + d.date },
    h('header', null, h('h3', null, dayLabel(d.date)), h('span', { class: 'meta' }, `${hours(d.active_min)} · ${money(d.cost)}`)),
    d.sessions.map((s) => sessionRow(s, key)),
    d.more.n ? h('p', { class: 'small-runs', title: 'Short or automatic runs with nothing typed: background helpers, checks, retries' },
      `+ ${d.more.n} small background run${d.more.n === 1 ? '' : 's'} · ${hours(d.more.active_min)} · ${money(d.more.cost)}`) : null,
    commits.length ? h('details', { class: 'changes' },
      h('summary', null, h('span', { class: 'chip-c' }, `${commits.length} change${commits.length === 1 ? '' : 's'} to the code`),
        h('span', { class: 'first' }, plain(commits[0].subject))),
      h('ul', null, commits.map((c) => h('li', null, h('span', { class: 't' }, c.time), plain(c.subject))))) : null);
}

function renderGame() {
  const key = route().key;
  const view = $('view');
  if (!game || game.game.key !== key) return;
  const g = game.game;
  const list = h('section', { class: 'days' }, game.days.slice(0, shownDays).map((d) => dayCard(d, key)),
    game.days.length > shownDays ? h('button', { class: 'older', onclick: () => { shownDays += DAYS_PAGE * 2; renderGame(); } },
      `Show older days (${game.days.length - shownDays} more)`) : null);
  put(view, picker(), h('div', { class: 'game-head' }, h('h1', null, g.name), h('span', null, g.what)), tiles(g), strip(game.days), list);
}

async function openGame(key) {
  const cached = snap.get('buildlog.game.' + key);
  const behind = !!route().session;  // loading the game under an open session: never paint over it
  if (cached && (!game || game.game.key !== key)) { game = cached; shownDays = DAYS_PAGE; if (!behind) renderGame(); }
  else if (!behind && (!game || game.game.key !== key)) put($('view'), picker(), h('p', { class: 'loading' }, 'Loading…'));
  const fresh = await api('/api/buildlog/game?project=' + encodeURIComponent(key));
  if (route().key !== key) return;
  if (!game || game.game.key !== key) shownDays = DAYS_PAGE;
  game = fresh;
  snap.set('buildlog.game.' + key, { game: fresh.game, days: fresh.days.slice(0, DAYS_PAGE) });
  if (!route().session) renderGame();
}

// ------------------------------------------------------------------ one session

function lightbox(paths, i) {
  const box = $('lightbox');
  const show = () => {
    put(box, h('img', { src: img(paths[i], 1024), alt: `Screenshot ${i + 1} of ${paths.length}` }),
      h('span', { class: 'count' }, `${i + 1} / ${paths.length}`),
      i > 0 ? h('button', { class: 'nav prev', 'aria-label': 'Previous', onclick: (e) => { e.stopPropagation(); i--; show(); } }, '‹') : null,
      i < paths.length - 1 ? h('button', { class: 'nav next', 'aria-label': 'Next', onclick: (e) => { e.stopPropagation(); i++; show(); } }, '›') : null);
  };
  show();
  box.hidden = false;
  box.onclick = () => { box.hidden = true; };
}

async function openSession(key, machine, id) {
  const view = $('view');
  put(view, h('a', { class: 'back', href: `#/${key}` }, '‹ ', (games || []).find((g) => g.key === key)?.name || 'Back'), h('p', { class: 'loading' }, 'Loading the session…'));
  window.scrollTo(0, 0);
  const r = await api(`/api/buildlog/session?machine=${encodeURIComponent(machine)}&session=${encodeURIComponent(id)}`);
  if (route().session?.id !== id) return;
  const s = r.session;
  const allPics = r.turns.flatMap((t) => t.shots);
  const turn = (t) => {
    const pics = t.shots;
    const at = allPics.indexOf(pics[0]);
    return h('section', { class: 'turn' },
      t.prompt ? h('div', { class: 'ask' }, h('span', { class: 'who' }, window.BEACON.owner.name), h('div', { class: 'md', html: markdown(t.prompt) }))
        : h('div', { class: 'ask auto' }, h('span', { class: 'who' }, 'Started on its own')),
      pics.length ? h('div', { class: 'shots' }, pics.map((p, j) => h('button', { onclick: () => lightbox(allPics, at + j), 'aria-label': 'Open screenshot' },
        h('img', { src: img(p, 360), alt: '', loading: 'lazy' })))) : null,
      t.nsteps ? h('details', { class: 'steps' }, h('summary', null, `${t.nsteps} step${t.nsteps === 1 ? '' : 's'} in between`),
        h('ol', null, t.steps.map((x) => h('li', null, plain(x))))) : null,
      t.reply ? h('div', { class: 'reply' }, h('span', { class: 'who' }, 'Claude'), h('div', { class: 'md', html: markdown(t.reply) })) : null,
      h('span', { class: 'meta turn-meta' }, `${fmtTime.format(new Date(t.ts * 1000))} · ${t.calls} steps · ${money(t.cost)}`));
  };
  const sameDay = fmtDay.format(new Date(s.start * 1000)) === fmtDay.format(new Date(s.end * 1000));
  const when = sameDay
    ? `${fmtDay.format(new Date(s.start * 1000))}, ${fmtTime.format(new Date(s.start * 1000))}–${fmtTime.format(new Date(s.end * 1000))}`
    : `${fmtDay.format(new Date(s.start * 1000))} ${fmtTime.format(new Date(s.start * 1000))} – ${fmtDay.format(new Date(s.end * 1000))} ${fmtTime.format(new Date(s.end * 1000))}`;
  put(view, [
    h('a', { class: 'back', href: `#/${key}` }, '‹ ', r.game?.name || 'Back'),
    h('div', { class: 'sess-head' },
      h('h1', null, s.title || plain(s.first_prompt).slice(0, 80) || 'Session'),
      h('p', { class: 'meta' }, `${when} · on ${s.machine}`)),
    h('section', { class: 'tiles' },
      [[hours(s.active_min), 'agent time'], [money(s.cost), 'API value'], [String(s.prompts), 'asks'], [String(r.shots), 'screenshots']]
        .map(([v, k]) => h('div', { class: 'tile' }, h('div', { class: 'v' }, v), h('div', { class: 'k' }, k)))),
    r.artifacts.length ? h('section', { class: 'panel links' }, h('h2', null, 'Pages it made'),
      r.artifacts.map((u) => h('a', { href: u, target: '_blank', rel: 'noopener' }, u.replace('https://', '')))) : null,
    ...(r.turns.length ? r.turns.map(turn) : [h('p', { class: 'loading' }, 'No conversation kept for this one.')])]);
}

// ------------------------------------------------------------------ boot

async function load() {
  showError(null);
  const r = route();
  try {
    if (!games || !r.key) {
      const fresh = await api('/api/buildlog');
      games = fresh.games;
      snap.set('buildlog.games', games);
    } else {
      api('/api/buildlog').then((f) => { games = f.games; snap.set('buildlog.games', games); }).catch(() => {});
    }
    if (!r.key) {
      if (games.length) { location.replace('#/' + games[0].key); return; }
      put($('view'), h('p', { class: 'loading' }, 'No game sessions yet.'));
      return;
    }
    if (r.session) {
      await openSession(r.key, r.session.machine, r.session.id);
      if (!game || game.game.key !== r.key) openGame(r.key).catch(() => {});
    } else {
      await openGame(r.key);
    }
  } catch (e) { showError(e); }
}

let lastKey = null;
window.addEventListener('hashchange', () => {
  const r = route();
  if (!r.session && game && game.game.key === r.key && lastKey === r.key) { renderGame(); restoreScroll(); }
  else load();
  lastKey = r.key;
});
let gameScroll = 0;
window.addEventListener('scroll', () => { if (!route().session) gameScroll = window.scrollY; }, { passive: true });
function restoreScroll() { requestAnimationFrame(() => window.scrollTo(0, gameScroll)); }
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('lightbox').hidden = true; });
if (games && route().key) { lastKey = route().key; }
load();
