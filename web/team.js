'use strict';
// Team: who works on what right now. The owner's card (team lead) first, then one card per project his AI team is on,
// on the page itself, no stage behind them. Plain words only: kind of work on the tag, a headline, how many helpers
// work or wait for him, what it should change. Projects nobody is on sit in one "Resting" line.
// Reads GET /api/team (hub/studio.py).
// On top: the results strip, only what reached people (reach, users, paying, Kč, shipped, emails, calls), all with
// equal weight, for today / this week / this month. Reads GET /api/outcomes (hub/outcomes.py); fills itself.

const REFRESH_MS = 15000;

// The kind of work, as a person doing it.
const KIND = {
  update: { label: 'Next update', who: '👷', c: '#5b9dff' },
  fix: { label: 'Fixing', who: '🧑‍🔧', c: '#ff8a5c' },
  players: { label: 'Getting players', who: '🧑‍🎤', c: '#2fd39a' },
  numbers: { label: 'Studying numbers', who: '🧑‍🔬', c: '#a78bfa' },
  money: { label: 'Earning more', who: '🧑‍💼', c: '#fbbf24' },
  content: { label: 'Making content', who: '🧑‍🎨', c: '#f472b6' },
  customers: { label: 'Finding customers', who: '🧑‍🏫', c: '#38bdf8' },
  trends: { label: 'Hunting trends', who: '🕵️', c: '#c084fc' },
  hq: { label: 'Behind the scenes', who: '🧑‍💻', c: '#94a3b8' },
};
const AMBER = '#f5a524';

let data = snap.get('team2');
let shown = '';
let out = snap.get('outcomes1');
let period = (() => { try { return localStorage.getItem('hub.outcomes.period') || 'week'; } catch { return 'week'; } })();
let open = null;  // the card whose sources are shown
const PERIODS = [['today', 'Today'], ['week', 'This week'], ['month', 'This month']];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function num(v, unit) {
  if (v == null) return '—';
  const s = Math.abs(v) >= 10000 ? (v / 1000).toFixed(v >= 100000 ? 0 : 1).replace(/\.0$/, '') + 'k' : Math.round(v).toLocaleString('en-US');
  return unit === 'kc' ? s + ' Kč' : s;
}
const kShort = (v) => (v >= 10000 ? Math.round(v / 1000) + 'k' : v >= 1000 ? (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(v));
const BURN_WORD = { today: 'daily costs', week: 'costs so far this week', month: 'monthly costs' };
const dayShort = (d) => `${+d.slice(8, 10)} ${MON[+d.slice(5, 7) - 1]}`;

// one line under the number: a source that failed (orange); else one that reports late or is held back (grey);
// else that it is current
function ageLine(c) {
  const bad = c.parts.filter((p) => p.state === 'down');
  if (bad.length) return { warn: true, text: bad.map((p) => `${p.short || p.label}: ${p.age}`).join(' · ') };
  const held = c.parts.filter((p) => p.ok && p.counted === false && p.value).map((p) => `${p.short || p.label} not cashable yet`);
  const late = c.parts.filter((p) => p.state === 'late' || /^(through|counted)/.test(p.age)).map((p) => p.age);
  const text = [...held, ...new Set(late)];
  return { warn: false, text: text.length ? text.join(' · ') : 'up to date' };
}

function card(c) {
  const parts = c.parts.filter((p) => p.ok && p.value && p.counted !== false).map((p) => `${p.short || p.label} ${num(p.value, p.unit)}`);
  const age = ageLine(c);
  const isOpen = open === c.key;
  let bar = null;
  if (c.key === 'money' && c.burn) {
    const pct = Math.min(100, Math.max(c.value ? 2 : 0, ((c.value || 0) / c.burn.low) * 100));
    bar = h('div', { class: 'burn' },
      h('div', { class: 'burn-track' }, h('i', { style: `width:${pct}%` })),
      h('span', null, `of ${kShort(c.burn.low)}–${kShort(c.burn.high)} Kč ${BURN_WORD[period] || ''}`));
  }
  return h('button', { class: 'res' + (c.value ? ' has' : '') + (isOpen ? ' open' : ''), type: 'button', 'aria-expanded': String(isOpen),
    onclick: () => { open = isOpen ? null : c.key; renderResults(); } },
    h('span', { class: 'res-label' }, h('i', { 'aria-hidden': 'true' }, c.icon), c.label),
    h('span', { class: 'res-num' + (c.value == null ? ' none' : '') }, num(c.value, c.unit)),
    bar,
    h('span', { class: 'res-parts' }, parts.length ? parts.join(' · ')
      : c.value != null ? 'none yet' : c.parts.some((p) => p.state === 'down') ? 'not read' : 'not reported yet'),
    h('span', { class: 'res-age' + (age.warn ? ' warn' : '') }, age.text));
}

function detail(c) {
  const rows = c.parts.map((p) => h('li', { class: p.ok ? '' : 'down' },
    h('span', { class: 'd-what' }, p.label),
    h('b', { class: p.counted === false ? 'not-counted' : null }, p.ok ? num(p.value, p.unit) : 'not read'),
    h('a', { href: p.link, target: p.link.startsWith('/') ? null : '_blank', rel: 'noopener' }, p.source + ' ↗'),
    h('span', { class: 'd-age' }, p.age)));
  const items = (c.items || []).slice(0, 12).map((x) => h('li', null, h('span', { class: 'd-day' }, dayShort(x.day)), x.what));
  const note = { money: 'App Store proceeds after Apple\'s cut, bank inflows except his own transfers, and his half of the Robux at Roblox\'s cash-out rate once 30,000 R$ can be cashed out.',
    paying: 'People who paid: App Store purchases and subscriptions, Roblox buyers (counted per day: someone who buys on two days counts twice).',
    reach: 'Views of his own TikTok videos, Clean the Snow visits, App Store listing impressions.',
    shipped: 'What went live: App Store versions, Roblox updates, sites deployed to production.' }[c.key];
  return h('div', { class: 'res-detail' },
    h('div', { class: 'd-head' }, h('b', null, `${c.icon} ${c.label}`), note ? h('span', null, note) : null),
    h('ul', { class: 'd-parts' }, rows),
    items.length ? h('ul', { class: 'd-items' }, items) : null);
}

function renderResults() {
  const box = $('results');
  if (!box || !out) return;
  const p = out.periods[period] || out.periods.week;
  const cards = p.cards;
  const sel = cards.find((c) => c.key === open);
  const mins = out.collected_at ? Math.round((Date.now() / 1000 - out.collected_at) / 60) : null;
  box.replaceChildren(...[  // replaceChildren would print a null
    h('div', { class: 'res-top' },
      h('h1', null, 'Results'),
      h('nav', { class: 'period', 'aria-label': 'Period' }, PERIODS.map(([k, label]) => h('button', { type: 'button', class: k === period ? 'on' : null,
        'aria-pressed': String(k === period), onclick: () => { period = k; try { localStorage.setItem('hub.outcomes.period', k); } catch { /* blocked */ } renderResults(); } }, label)))),
    out.stale ? h('p', { class: 'res-stale' }, `⚠️ Numbers last came in ${mins == null ? 'never' : mins < 120 ? mins + ' min' : Math.round(mins / 60) + ' h'} ago: the rank collector stopped`) : null,
    // the open card's sources right under its own row (the grid backfills the row around it)
    h('div', { class: 'res-grid' }, cards.flatMap((c) => (c === sel ? [card(c), detail(c)] : [card(c)])))].filter(Boolean));
}

// A running task's own plan (hub/progress.py): one thin bar, a segment per step, and the step words. One bar per card,
// for the job worked last, under that job's title so the bar says what it measures (two bars in one card read as noise,
// his note 2026-11-23); another running job on the project is one plain line under it.
function job(j) {
  const now = j.steps[j.at];
  const left = j.left === 'waiting for you' ? 'Waiting for you'
    : j.left ? `⏱️ ${j.left} left${j.first_guess ? ' (first guess)' : ''}` : null;
  return h('div', { class: 'job' },
    h('span', { class: 'job-title' }, j.title),
    h('div', { class: 'job-bar', role: 'img', 'aria-label': `Step ${j.at + 1} of ${j.steps.length}` },
      j.steps.map((_, i) => h('i', { class: i < j.at ? 'done' : i === j.at ? 'now' : null }))),
    h('span', { class: 'job-step' }, h('b', null, `Step ${j.at + 1} of ${j.steps.length}`), ` · ${now}`),
    left || j.then ? h('span', { class: 'job-left' + (j.owner[j.at] ? ' wait' : '') },
      [left, j.then ? `then ${j.then}` : null].filter(Boolean).join(' · ')) : null);
}

function guessLine(g) {
  if (!g) return null;
  if (!g.jobs) return h('p', { class: 'resting' }, h('b', null, '⏱️ Time guesses'), 'learning: graded when the first guessed jobs finish');
  return h('p', { class: 'resting' }, h('b', null, '⏱️ Time guesses'), `off by ~${g.off_pct}% on the last ${g.jobs} ${g.jobs === 1 ? 'job' : 'jobs'}`);
}

function node(p) {
  const k = KIND[p.kind] || KIND.update;
  const c = p.waiting ? AMBER : k.c;
  const many = (n, one, more) => `${n} ${n === 1 ? one : more}`;
  const idle = p.helpers - p.busy;
  return h('article', { class: 'node' + (p.waiting ? ' waiting' : ' busy'), style: `--c:${c}` },
    h('div', { class: 'node-top' },
      h('span', { class: 'tile', 'aria-hidden': 'true' }, k.who),
      h('div', { class: 'id' }, h('h2', null, p.name), h('span', { class: 'tag' }, p.waiting ? 'Waiting for you' : k.label))),
    h('p', { class: 'line' }, p.headline),
    p.jobs && p.jobs.length ? job(p.jobs[0]) : null,
    p.jobs && p.jobs.length > 1 ? h('p', { class: 'job-more' }, `Also: ${p.jobs.slice(1).map((j) => j.title).join(' · ')}`) : null,
    h('footer', { class: 'node-foot' },
      p.busy ? h('span', { class: 'state live' }, h('i'), many(p.busy, 'helper working', 'helpers working')) : null,
      idle ? h('span', { class: 'state wait' }, h('i'), `${idle} waiting for you`) : null,
      p.aim ? h('span', { class: 'aim' }, '🎯 ', p.aim) : null));
}

function lead(real) {
  const helpers = real.reduce((a, p) => a + (p.helpers || 1), 0);
  const busy = real.reduce((a, p) => a + (p.busy || 0), 0);
  return h('article', { class: 'node lead' },
    h('div', { class: 'node-top' },
      h('span', { class: 'tile', 'aria-hidden': 'true' }, '🧑‍✈️'),
      h('div', { class: 'id' }, h('h2', null, window.BEACON.owner.name), h('span', { class: 'tag' }, 'Team lead'))),
    h('div', { class: 'lead-nums' },
      h('span', null, h('b', null, String(busy)), busy === 1 ? 'helper working' : 'helpers working'),
      h('span', null, h('b', null, String(helpers - busy)), 'waiting for you'),
      h('span', null, h('b', null, String(real.length)), real.length === 1 ? 'project' : 'projects')));
}

function render() {
  if (!data) { if (out && !$('results')) $('view').replaceChildren(h('section', { class: 'results', id: 'results' })); renderResults(); return; }
  const key = JSON.stringify([data.projects || [], (data.resting || []).map((r) => r.name), data.guesses]);
  if (key === shown && $('results')) { renderResults(); return; }
  shown = key;
  const real = data.projects || [];
  const resting = (data.resting || []).map((r) => r.name);

  $('view').replaceChildren(
    h('section', { class: 'results', id: 'results', 'aria-label': 'Results' }),
    h('section', { class: 'studio', 'aria-label': 'Working now' },
      h('div', { class: 'res-top' }, h('h1', null, 'Working now'),
        real.some((p) => p.busy) ? h('span', { class: 'on-air' }, h('i'), 'Live') : null),
      h('div', { class: 'crew' }, lead(real), real.map(node)),
      real.length ? null : h('p', { class: 'quiet' }, '☕ Quiet right now: no helper at work'),
      resting.length ? h('p', { class: 'resting' }, h('b', null, 'Resting'), resting.join(' · ')) : null,
      guessLine(data.guesses)));
  renderResults();
}

async function loadResults() {
  try {
    out = await api('/api/outcomes');
    renderResults();
    snap.set('outcomes1', out);
  } catch { /* keep the last numbers; the team view shows the error */ }
}

async function load() {
  loadResults();
  try {
    data = await api('/api/team');
    $('error').hidden = true;
    render();
    snap.set('team2', data);
  } catch (e) {
    $('error').textContent = e.message;
    $('error').hidden = false;
  }
}

render();
load();
setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
