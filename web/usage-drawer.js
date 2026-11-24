// Usage page session drawer: one session's turns, tools and cost.
import { $, api, h } from './shared.js';
import { pname } from './names.js';
import { MACHINE_ICON } from './usage-header.js';
import { go, setFilter, state } from './usage-state.js';
import { costText, highlight, projectTitle } from './usage-tables.js';
import { hideTip } from './usage-timeline.js';
import { CLICK, count, fmtDateTime, fmtDur, fmtInt, fmtMin, fmtTime, fmtTok, fold, localDate, prettyDate, projectColor, sessName, sum, swatch } from './usage-util.js';

let drawerSession = null;

let drawerOpener = null;
let drawerTitle = null;  // the list row's name (its task or a model-written one): the session API doesn't carry it
export async function openSession(ref) {
  hideTip();
  drawerTitle = ref.title || null;
  if ($('drawer').hidden) drawerOpener = document.activeElement;
  for (const el of /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('header.top, main'))) el.inert = true;
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
  /** @type {HTMLElement} */ ($('drawer').querySelector('.drawer-close')).focus();
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
    if (text.scrollHeight > text.clientHeight + 2) /** @type {HTMLElement} */ (text.nextElementSibling).hidden = false;
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

export function closeDrawer() {
  if ($('drawer').hidden) return;
  $('drawer').hidden = true;
  drawerSession = null;
  for (const el of /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll('header.top, main'))) el.inert = false;
  if (drawerOpener?.isConnected) drawerOpener.focus();
  drawerOpener = null;
}
