// Tasks: the hub's task list (docs/specs/2026-10-06-tasks-design.md). Plain JS, no build.
// Views by hash: #/now (home), #/backlog[/<project>], #/triage[/stale|high], #/task/<id>, #/search/<q>.
// Old links (#/projects, #/project/<name>, #/stale) land on their new homes.
import { $, ago, api, closeMenu, DAY, h, markdown, openMenu, pragueToday, setDecisionsBadge, showError, snap, toast } from './shared.js';
import { pname, projChip, shortTitle } from './names.js';
import { qCard, slideshow } from './qcards.js';
import './viewer.js';

const PRIORITY = { 1: 'urgent', 2: 'high', 3: 'normal', 4: 'low' };
const STALE_DAYS = 14;

const state = { tasks: [], live: [], projects: [], questions: [], loaded: false };

// The agent: a task tagged `claude` (his go-ahead) waits for the task agent on its home machine
// (client/task-agent), which claims it, links its session and works it to a PR.
const AGENT_TAG = 'claude';
const AGENT_HOME = window.BEACON.hub.agent_home;
const PR_URL = /https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+/;
const DONE_TEST = /^#{1,4}\s*done[- ]test/im;

// ------------------------------------------------------------------ helpers (the shared ones: shared.js)

function dueLabel(due) {
  if (!due) return null;
  const day = due.slice(0, 10), time = due.slice(11);
  const today = pragueToday();
  let word;
  if (day < today) word = `overdue · ${day.slice(5).replace('-', '/')}`;
  else if (day === today) word = 'today';
  else if (day === pragueToday(1)) word = 'tomorrow';
  else word = new Date(day + 'T12:00').toLocaleDateString(window.hubLocale || 'en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  return { text: time ? `${word} ${time}` : word, overdue: day < today, today: day === today };
}

function sourceLink(source) {
  if (!source) return null;
  const [kind, ...rest] = source.split(':');
  const id = rest.join(':');
  if (kind === 'clickup') return h('a', { href: `https://app.clickup.com/t/${encodeURIComponent(id)}`, target: '_blank', rel: 'noopener' }, 'from ClickUp');
  if (kind === 'session') return h('span', {}, `from session ${id.split(':').pop().slice(0, 8)}`);
  return h('span', {}, `from ${source}`);
}

// ------------------------------------------------------------------ load + save

function take([t, live, p, q]) {
  state.tasks = t.tasks;
  state.questions = q ? q.questions : [];
  state.live = live ? live.machines.flatMap((m) => m.sessions) : [];
  state.projects = p.projects;
  state.loaded = true;
}

async function load() {
  try {
    const got = await Promise.all([api('/api/tasks?status=open&limit=2000&brief=1'), api('/api/live').catch(() => null),
      api('/api/tasks/projects'), api('/api/questions?status=open&limit=200').catch(() => null)]);
    take(got);
    snap.set('tasks', got);
    if (got[3]) setDecisionsBadge(got[3].questions.length);
    showError(null);
  } catch (e) {
    showError(e);
  }
  // A poll that brought nothing new leaves the page alone: open folds, scroll and focus stay (no blink).
  const sig = JSON.stringify([state.tasks, state.projects, state.questions.map((q) => [q.id, q.updated_at]),
    state.live.map((x) => [x.id, x.state, x.project]), Math.floor(Date.now() / 60000)]);  // "touched 5 min ago" moves once a minute
  if (sig === state.sig) return;
  state.sig = sig;
  renderProjects();
  // never under an open editor or a half-typed answer
  if (current().view !== 'task' && !document.activeElement?.matches('.answer-own input')) render();
  else state.sig = null;
}

async function save(id, fields, undo) {
  try {
    const t = listed(await api(`/api/tasks/${id}`, fields));
    const i = state.tasks.findIndex((x) => x.id === id);
    if (t.status === 'todo' || t.status === 'doing') {
      if (i >= 0) state.tasks[i] = t; else state.tasks.unshift(t);
    } else if (i >= 0) state.tasks.splice(i, 1);
    if (undo) toast(undo.text, () => save(id, undo.fields));
    // The task page updates its own fields; rebuilding it would throw away a half-typed edit.
    if (current().view !== 'task') render();
    return t;
  } catch (e) {
    showError(e);
  }
}

// One task as the list API gives it: session ids and the latest note (a single task comes
// with its sessions as objects and every comment).
function listed(t) {
  const last = t.comments && t.comments[t.comments.length - 1];
  return { ...t, sessions: (t.sessions || []).map((s) => s.session_id || s),
    last_note: t.comments ? (last ? { author: last.author, at: last.at, text: last.text } : null) : t.last_note };
}

// ------------------------------------------------------------------ the agent

const withAgent = (t) => t.tags.includes(AGENT_TAG) && !t.tags.includes('parked');
const agentAsks = (t) => !withAgent(t) && t.last_note && t.last_note.author !== window.BEACON.owner.id && t.last_note.text.startsWith('⏳');
const readyToHand = (t) => t.status === 'todo' && !withAgent(t) && (t.has_done_test ?? DONE_TEST.test(t.body));

// What the agent is doing with a task: from its linked sessions that are live right now
// (`live`: session objects with state, machine, doing) and its latest note.
function agentStatus(t, live) {
  const s = [...live].sort((a, b) => (b.last || 0) - (a.last || 0))[0];
  const pr = t.last_note && (t.last_note.text.match(PR_URL) || [])[0];
  if (t.status === 'todo') return { cls: 'queued', text: `Queued: the agent on ${AGENT_HOME} picks it up within a minute` };
  if (s && s.state === 'working') return { cls: 'working', text: `Working on ${s.machine}${s.doing ? ` · ${s.doing}` : ''}` };
  if (pr) return { cls: 'review', text: 'PR open: yours to review', link: pr };
  if (s && s.state === 'waiting') return { cls: 'waiting', text: `Waiting on ${s.machine}${s.doing ? ` · ${s.doing}` : ''}` };
  return { cls: 'doing', text: 'On it' };
}
const liveFor = (t) => state.live.filter((s) => (t.sessions || []).includes(s.id));
const AGENT_ORDER = { working: 0, waiting: 1, review: 2, queued: 3, doing: 4 };

const handToAgent = (t) => save(t.id, { tags: [...new Set([...t.tags, AGENT_TAG])], status: 'todo' },
  { text: `Handed to the agent: ${t.title}`, fields: { tags: t.tags, status: t.status } });
const takeBack = (t) => save(t.id, { tags: t.tags.filter((x) => x !== AGENT_TAG) },
  { text: `Back with you: ${t.title}`, fields: { tags: t.tags } });

// ------------------------------------------------------------------ rows

function taskMenu(t) {
  const items = [
    withAgent(t) ? ['Take back from the agent', () => takeBack(t)] : ['Hand to the agent', () => handToAgent(t)],
    '-',
    [t.status === 'doing' ? 'Back to to do' : 'Start (doing)', () => save(t.id, { status: t.status === 'doing' ? 'todo' : 'doing' })],
    ['Snooze to tomorrow', () => save(t.id, { due: pragueToday(1) }, { text: 'Snoozed to tomorrow', fields: { due: t.due } })],
    ['Snooze a week', () => save(t.id, { due: pragueToday(7) }, { text: 'Snoozed a week', fields: { due: t.due } })],
    t.due && ['Clear due date', () => save(t.id, { due: null })],
    '-',
    ...[1, 2, 3, 4].map((p) => [`Priority: ${PRIORITY[p]}${t.priority === p ? ' ✓' : ''}`, () => save(t.id, { priority: p })]),
    t.priority && ['No priority', () => save(t.id, { priority: null })],
    '-',
    ['Drop', () => save(t.id, { status: 'dropped' }, { text: `Dropped: ${t.title}`, fields: { status: t.status } })],
  ].filter(Boolean).map((x) => x === '-' ? ['-'] : x);
  return items;
}

function row(t, opts = {}) {
  const due = dueLabel(t.due);
  return h('li', { class: `task p${t.priority || 0}${t.status === 'doing' ? ' doing' : ''}` },
    h('button', {
      class: 'check', title: 'Done', 'aria-label': `Done: ${t.title}`,
      onclick: () => save(t.id, { status: 'done' }, { text: `Done: ${t.title}`, fields: { status: t.status } }),
    }),
    h('a', { class: 'body', href: `#/task/${t.id}` },
      h('span', { class: 'title' }, opts.mine ? moveText(t) : opts.noProject ? t.title : shortTitle(t)),
      opts.mine && moveText(t) !== shortTitle(t) && h('span', { class: 'for' }, shortTitle(t)),
      h('span', { class: 'meta' },
        !opts.noProject && projChip(t.project),
        !opts.mine && whose(t) === 'agents' && h('span', { class: 'tpill doing' }, withAgent(t) && t.status === 'todo' ? '🤖 Queued' : '🤖 Agent on it'),
        !opts.mine && whose(t) === 'mine' && h('span', { class: 'tpill mine' }, '👤 Yours'),
        duePill(due),
        t.priority && t.priority <= 2 && h('span', { class: `prio prio${t.priority}` }, PRIORITY[t.priority]),
        ...t.tags.filter((x) => !(opts.agent && x === AGENT_TAG)).map((x) => h('span', { class: `tag${x === AGENT_TAG ? ' claude' : ''}` }, x)),
        opts.age && h('span', {}, `touched ${ago(t.updated_at)}`)),
      opts.agent && agentLine(agentStatus(t, liveFor(t))),
      ),
    opts.actions ? opts.actions(t) : moreButton(t));
}

const moreButton = (t) => h('button', { class: 'more', 'aria-label': 'More', onclick: (e) => { e.stopPropagation(); openMenu(e.currentTarget, taskMenu(t)); } }, '···');

function agentLine(st) {
  return h('span', { class: `agent-line ${st.cls}` }, h('span', { class: `state ${st.cls}` }), h('span', {}, st.text),
    st.link && h('a', { href: st.link, target: '_blank', rel: 'noopener', onclick: (e) => e.stopPropagation() }, 'Open PR'));
}

function section(title, tasks, opts = {}) {
  if (!tasks.length && !opts.always) return null;
  let shown = Math.min(tasks.length, opts.limit ?? Infinity);
  const list = h('ul', { class: 'tasks' }, tasks.slice(0, shown).map((t) => row(t, opts)));
  const label = () => opts.step ? `Show ${Math.min(opts.step, tasks.length - shown)} more` : `${tasks.length - shown} ${opts.more || 'more'} →`;
  // A capped list either links to where the rest live, or grows in steps, or opens fully.
  const more = tasks.length > shown && (opts.moreHref ? h('a', { class: 'more-link', href: opts.moreHref }, label()) :
    h('button', { class: 'show-all', onclick: (e) => {
      const to = opts.step ? shown + opts.step : tasks.length;
      list.append(...tasks.slice(shown, to).map((t) => row(t, opts)));
      shown = Math.min(to, tasks.length);
      if (shown >= tasks.length) e.currentTarget.remove(); else e.currentTarget.textContent = label();
    } }, label()));
  return h('section', { class: 'group' }, title && h('h2', {}, title, !opts.noCount && h('span', { class: 'n' }, tasks.length)),
    tasks.length ? list : h('p', { class: 'muted empty' }, opts.empty || 'Nothing here.'), more);
}

function agentRow(s) {
  return h('li', { class: 'agent' },
    h('span', { class: `state ${s.state}` }),
    h('span', { class: 'body' },
      h('span', { class: 'title' }, s.title || s.project),
      h('span', { class: 'meta' }, h('span', { class: 'proj' }, s.project), h('span', {}, s.machine),
        s.idle_s != null && h('span', {}, `waiting ${ago(Date.now() / 1000 - s.idle_s)}`.replace(' ago', ''))),
      s.doing && h('span', { class: 'asks' }, s.doing)));
}

// ------------------------------------------------------------------ views

const byUrgency = (a, b) => (a.priority || 5) - (b.priority || 5) || (a.due || '9').localeCompare(b.due || '9') || b.updated_at - a.updated_at;

// Home shows what is alive. Parked tasks wait in the backlog; ones untouched for STALE_DAYS wait in Clean up
// until kept (which touches them) or dropped, so months-old dates don't bury today.
const isParked = (t) => t.tags.includes('parked');
const isStale = (t) => !isParked(t) && t.updated_at < Date.now() / 1000 - STALE_DAYS * DAY;
const NEXT_CAP = 4, MINE_CAP = 8, AGENT_PROJECTS = 5;

const snooze = (t, days, text) => save(t.id, { due: pragueToday(days) }, { text, fields: { due: t.due } });
const done = (t) => save(t.id, { status: 'done' }, { text: `Done: ${t.title}`, fields: { status: t.status } });
const drop = (t) => save(t.id, { status: 'dropped' }, { text: `Dropped: ${t.title}`, fields: { status: t.status } });

// Whose move a task waits on (2026-11-24: "very unclear when I wake up"; 70 of 75 started tasks were agent
// work, and they flooded Today). His: an agent asks him, a blocking card, his own (👤) step, or a dated task
// nobody else holds. Agents: everything started or handed over. Later: the rest.
const dueBy = (t, day) => t.due && t.due.slice(0, 10) <= day;
function whose(t, today = pragueToday()) {
  if (isParked(t)) return 'parked';
  // A decision is never a task here (2026-11-24: "you mix decisions with tasks"): a blocking card, an agent's
  // question, a step that says to answer on Decisions or a "Decide:" task all live on Decisions alone.
  if (isDecision(t)) return 'decision';
  if (t.next_step && t.next_step.mine) return 'mine';
  if (withAgent(t) || t.status === 'doing' || t.next_step) return 'agents';
  if (t.due && t.due.slice(0, 10) === today) return 'mine';
  if (dueBy(t, today)) return 'late';  // its day passed: Clean up > Late gives it a new one, Today stays today's
  return isStale(t) ? 'stale' : 'later';
}
// His say, not his hands: an OK, a pick, an answer, a question mark.
const SAYS = /decisions|\b(OKs?|approves?|decides?|answers?|says?|picks?|confirms?)\b|\?\s*$/i;
const isDecision = (t) => t.waiting_on_him || agentAsks(t) || /^decide\b/i.test(t.title) || /\?\s*$/.test(t.title)
  || (t.next_step && t.next_step.mine && SAYS.test(t.next_step.label));
// The words he acts on: his step, else the task itself.
const moveText = (t) => t.next_step && t.next_step.mine ? t.next_step.label : shortTitle(t);
const mineOrder = (a, b) => (a.due || '9').localeCompare(b.due || '9') || byUrgency(a, b);

// The home screen, read on waking: how much is his today in three numbers, then his moves (the step as the
// headline), then the agents' work as one line per project, then a short Up next.
function viewHome() {
  const today = pragueToday();
  const pile = { mine: [], agents: [], later: [], late: [] };
  for (const t of state.tasks) { const w = whose(t, today); if (pile[w]) pile[w].push(t); }
  const mine = pile.mine.sort(mineOrder);
  const later = pile.later.sort(byUrgency);
  const hero = mine[0];
  const lateN = pile.late.length;
  const working = state.live.filter((s) => s.state === 'working').length;

  const stats = h('nav', { class: 'day-stats', 'aria-label': 'Today in numbers' },
    stat('#/now', mine.length, mine.length === 1 ? 'move for you' : 'moves for you', mine.length ? 'hot' : ''),
    stat('#/backlog', pile.agents.length, working ? `agent tasks · ${working} live` : 'agent tasks', ''));

  return [
    stats,
    hero ? heroCard(hero) : h('section', { class: 'allclear' }, h('div', { class: 'tick', 'aria-hidden': 'true' }, '✓'),
      h('h2', {}, 'Nothing on you'), h('p', {}, 'The agents have it. Pick something from Up next if you want.')),
    mine.length > 1 && section('Then', mine.slice(1), { mine: true, limit: MINE_CAP, step: 10, noCount: true }),
    lateN > 0 && h('nav', { class: 'jump' }, jump('#/triage/late', `${lateN} late`, 'their day passed: give each a new day or drop it')),
    section('Up next', later, { limit: NEXT_CAP, moreHref: '#/backlog', more: 'in Projects' }),
    agentsByProject(pile.agents),
    footJumps(),
  ];
}

const stat = (href, num, label, cls) => h('a', { class: `day-stat ${cls}`, href },
  h('strong', {}, String(num)), h('span', {}, label));

function footJumps() {
  const stale = state.tasks.filter((t) => isStale(t) && !withAgent(t) && !agentAsks(t)).length;
  const waiting = state.live.filter((s) => s.state === 'waiting').length;
  return h('nav', { class: 'jump' },
    jump('#/backlog', 'All by project', `${state.tasks.filter((t) => !isParked(t)).length} open`),
    stale > 0 && jump('#/triage', 'Clean up', `${stale} untouched for ${STALE_DAYS}+ days`),
    waiting > 0 && jump('/usage', 'Agent sessions', `${waiting} waiting for a reply`));
}

// What the agents hold, one line per project: how many, how many live now, and the freshest one's title.
function agentsByProject(tasks) {
  if (!tasks.length) return null;
  const by = new Map();
  for (const t of tasks) (by.get(t.project) || by.set(t.project, []).get(t.project)).push(t);
  const rows = [...by].map(([p, ts]) => {
    const live = ts.filter((t) => liveFor(t).some((s) => s.state === 'working')).length;
    const top = [...ts].sort((a, b) => b.updated_at - a.updated_at)[0];
    return { p, ts, live, top };
  }).sort((a, b) => b.live - a.live || b.ts.length - a.ts.length);
  return h('section', { class: 'group agents-by' }, h('h2', {}, 'Agents are on it', h('span', { class: 'n' }, tasks.length)),
    h('ul', { class: 'proj-lines' }, rows.slice(0, AGENT_PROJECTS).map(({ p, ts, live, top }) => h('li', {},
      h('a', { href: `#/backlog/${encodeURIComponent(p)}` },
        projChip(p),
        h('span', { class: 'pl-top' }, shortTitle(top)),
        h('span', { class: 'pl-n' }, live ? h('span', { class: 'state working' }) : null, `${ts.length}`))))),
    rows.length > AGENT_PROJECTS && h('a', { class: 'more-link', href: '#/backlog' }, `${rows.length - AGENT_PROJECTS} more projects →`));
}

const jump = (href, title, sub) => h('a', { href }, h('strong', {}, title), h('span', {}, sub), h('span', { class: 'arrow', 'aria-hidden': 'true' }, '→'));

// Due as a pill with an icon, red when late, so the line reads at a glance, not as three plain words.
const duePill = (due) => due && h('span', { class: `tpill due${due.overdue ? ' overdue' : due.today ? ' today' : ''}` },
  `${due.overdue ? '⚠️' : '📅'} ${due.text.charAt(0).toUpperCase() + due.text.slice(1)}`);

// His first move, big: the step he does as the headline, the task it moves underneath, and the three moves
// that matter. "Do now" sits on the card's top edge as a tab (2026-11-22).
function heroCard(t) {
  const due = dueLabel(t.due);
  const head = moveText(t), title = shortTitle(t);
  return h('section', { class: 'hero now' }, h('span', { class: 'kicker hero-tab' }, 'Your move'),
    h('div', { class: 'hero-top' }, projChip(t.project), duePill(due)),
    h('a', { class: 'hero-title', href: `#/task/${t.id}` }, head),
    // Only the task it moves, never the plan's inner words (paths, kill criteria) on the first screen.
    head !== title && h('p', { class: 'hero-for' }, 'for ', h('a', { href: `#/task/${t.id}` }, title)),
    h('div', { class: 'hero-actions' },
      h('button', { class: 'primary', onclick: () => done(t) }, '✓ Done'),
      h('button', { onclick: () => snooze(t, 1, 'Moved to tomorrow') }, 'Tomorrow'),
      h('button', { class: 'more', 'aria-label': 'More', onclick: (e) => { e.stopPropagation(); openMenu(e.currentTarget, taskMenu(t)); } }, '···')));
}

// Every open task by project, sorted the way he picks (remembered on this device). All: one block per project,
// his moves first; a project: its tasks split by whose move it is, parked ones folded at the bottom.
const WHOSE_RANK = { mine: 0, late: 1, agents: 2, later: 3, stale: 4, decision: 5, parked: 6 };
const SORTS = {
  whose: { label: 'Yours first', cmp: (a, b) => WHOSE_RANK[whose(a)] - WHOSE_RANK[whose(b)] || mineOrder(a, b) },
  due: { label: 'Due date', cmp: (a, b) => (a.due || '9').localeCompare(b.due || '9') || byUrgency(a, b) },
  touched: { label: 'Last touched', cmp: (a, b) => b.updated_at - a.updated_at },
  new: { label: 'Newest', cmp: (a, b) => b.created_at - a.created_at },
  priority: { label: 'Priority', cmp: byUrgency },
};
const PER_PROJECT = 3;
let sortKey = (() => { try { return localStorage.getItem('tasks.sort') || 'whose'; } catch { return 'whose'; } })();
if (!SORTS[sortKey]) sortKey = 'whose';

function sortSwitch() {
  return h('label', { class: 'sort' }, h('span', {}, 'Sort'),
    h('select', { 'aria-label': 'Sort tasks', onchange: (e) => {
      sortKey = e.target.value;
      try { localStorage.setItem('tasks.sort', sortKey); } catch { /* private window: sort just isn't kept */ }
      render();
    } }, Object.entries(SORTS).map(([k, d]) => h('option', { value: k, selected: k === sortKey }, d.label))));
}

function viewBacklog(project) {
  const cmp = SORTS[sortKey].cmp;
  const by = new Map();
  for (const t of state.tasks) (by.get(t.project) || by.set(t.project, []).get(t.project)).push(t);
  const count = (ts, w) => ts.filter((t) => whose(t) === w).length;
  const projects = [...by].map(([p, ts]) => ({ p, ts, live: ts.filter((t) => !isParked(t)).sort(cmp), mine: count(ts, 'mine') }))
    .filter((x) => x.live.length || x.p === project);
  // Blocks follow the sort (a project's best row decides its place); "Yours first" puts the most of his first.
  const blocks = [...projects].sort((a, b) => sortKey === 'whose' ? b.mine - a.mine || b.live.length - a.live.length
    : Number(!a.live.length) - Number(!b.live.length) || (a.live.length && b.live.length ? cmp(a.live[0], b.live[0]) : 0));
  projects.sort((a, b) => b.live.length - a.live.length);
  const chips = h('div', { class: 'chips', role: 'list' },
    h('a', { class: `pchip${project ? '' : ' on'}`, href: '#/backlog' }, 'All ', h('span', {}, projects.reduce((n, x) => n + x.live.length, 0))),
    projects.map(({ p, live, mine }) => h('a', { class: `pchip${p === project ? ' on' : ''}${mine ? ' has-mine' : ''}`, href: `#/backlog/${encodeURIComponent(p)}` },
      pname(p) || p, ' ', h('span', {}, live.length))));
  const bar = h('div', { class: 'backlog-bar' }, chips, sortSwitch());

  if (!project) {
    return [bar, blocks.map(({ p, live, mine }) => {
      const ts = live;
      const agents = count(live, 'agents');
      return h('section', { class: 'group proj-block' },
        h('a', { class: 'proj-head', href: `#/backlog/${encodeURIComponent(p)}` }, projChip(p),
          h('span', { class: 'proj-counts' }, mine > 0 && h('b', {}, `${mine} yours`), agents > 0 && h('span', {}, `${agents} with agents`),
            h('span', {}, `${live.length} open`)), h('span', { class: 'arrow', 'aria-hidden': 'true' }, '→')),
        h('ul', { class: 'tasks' }, ts.slice(0, PER_PROJECT).map((t) => row(t, { noProject: true }))),
        ts.length > PER_PROJECT && h('a', { class: 'more-link', href: `#/backlog/${encodeURIComponent(p)}` }, `${ts.length - PER_PROJECT} more →`));
    })];
  }

  const all = (by.get(project) || []);
  const live = all.filter((t) => !isParked(t)).sort(cmp);
  const parked = all.filter(isParked).sort(cmp);
  const agentsLive = liveIn(project);
  const split = sortKey === 'whose';
  const pick = (w) => live.filter((t) => whose(t) === w);
  return [
    bar,
    split ? [
      section('Yours', pick('mine'), { noProject: true, mine: true }),
      section('Agents are on it', pick('agents'), { noProject: true, limit: 10, step: 20 }),
      section('Late', pick('late'), { noProject: true, limit: 5, step: 20 }),
      section('Later', [...pick('later'), ...pick('stale')], { noProject: true, limit: 15, step: 30 }),
    ] : section(pname(project) || project, live, { noProject: true, always: true, limit: 40, step: 40, empty: 'No open tasks.' }),
    !live.length && split && h('p', { class: 'muted empty' }, 'Nothing open here.'),
    split && pick('decision').length > 0 && h('nav', { class: 'jump' },
      jump('/decisions', 'Decisions', `${pick('decision').length} in this project wait for your answer there`)),
    section('Parked', parked, { noProject: true, limit: 0, step: 20 }),
    // Live sessions last and without their last words: those are often questions, which belong on Decisions.
    agentsLive.length > 0 && h('section', { class: 'group agents' }, h('h2', {}, 'Agent sessions here now', h('span', { class: 'n' }, agentsLive.length)),
      h('ul', { class: 'tasks' }, agentsLive.map((x) => agentRow({ ...x, doing: null })))),
  ];
}

// Clean up, one card at a time: keep it, push it a week, or drop it. Two piles: untouched for
// weeks, and "high" priority (most of which isn't, so a list of them helps nobody).
const triageSeen = new Set();
const TRIAGE = {
  stale: { label: 'Untouched', pick: (t) => isStale(t), sort: (a, b) => a.updated_at - b.updated_at,
    why: `Open but nobody touched it for ${STALE_DAYS}+ days.` },
  late: { label: 'Late', pick: (t) => whose(t) === 'late', sort: (a, b) => a.due.localeCompare(b.due),
    why: 'Its day passed. Give it a real day, or let it go.' },
  high: { label: 'Marked high', pick: (t) => t.priority && t.priority <= 2 && !isStale(t), sort: (a, b) => a.created_at - b.created_at,
    why: 'Is it really urgent? Keep it high only if it is.' },
};

function viewTriage(pile) {
  triageKeys = {};  // an empty pile must not leave the last card's keys live
  const kind = TRIAGE[pile] ? pile : 'stale';
  const def = TRIAGE[kind];
  const pool = state.tasks.filter((t) => !withAgent(t) && !agentAsks(t) && def.pick(t));
  const left = pool.filter((t) => !triageSeen.has(t.id)).sort(def.sort);
  const t = left[0];
  const tabs = h('div', { class: 'chips' }, Object.entries(TRIAGE).map(([k, d]) =>
    h('a', { class: `pchip${k === kind ? ' on' : ''}`, href: `#/triage/${k}` }, d.label, ' ',
      h('span', {}, state.tasks.filter((x) => !withAgent(x) && !agentAsks(x) && d.pick(x) && !triageSeen.has(x.id)).length))));
  if (!t) return [tabs, h('section', { class: 'allclear' }, h('div', { class: 'tick', 'aria-hidden': 'true' }, '✓'),
    h('h2', {}, 'Pile cleared'), h('p', {}, h('a', { href: '#/now' }, 'Back to Today')))];
  const seen = () => { triageSeen.add(t.id); };
  const act = (fn) => async () => { if (await fn() !== undefined) seen(); render(); };
  const due = dueLabel(t.due);
  /** @type {[string, string, () => Promise<void>, string?][]} */
  const actions = kind === 'late' ? [
    ['Today', 't', act(() => save(t.id, { due: pragueToday() }, { text: 'Moved to today', fields: { due: t.due } })), 'primary'],
    ['In a week', 'w', act(() => save(t.id, { due: pragueToday(7) }, { text: 'Back in a week', fields: { due: t.due } }))],
    ['No date', 'n', act(() => save(t.id, { due: null }, { text: 'Date cleared', fields: { due: t.due } }))],
  ] : kind === 'high' ? [
    ['Not urgent', 'n', act(() => save(t.id, { priority: 3 }, { text: `Normal priority: ${t.title}`, fields: { priority: t.priority } })), 'primary'],
    ['Yes, urgent', 'k', act(() => save(t.id, {}))],
  ] : [
    ['Keep', 'k', act(() => save(t.id, t.tags.includes('parked') ? { tags: t.tags.filter((x) => x !== 'parked') } : {}, { text: `Kept: ${t.title}`, fields: {} })), 'primary'],
    ['In a week', 'w', act(() => save(t.id, { due: pragueToday(7) }, { text: 'Back in a week', fields: { due: t.due } }))],
  ];
  actions.push(['Drop', 'd', act(() => drop(t)), 'danger'], ['Agent', 'a', act(() => handToAgent(t))], ['Skip', 's', act(() => true)]);
  triageKeys = Object.fromEntries(actions.map(([, key, fn]) => [key, fn]));
  return [
    tabs,
    h('p', { class: 'muted intro' }, def.why, ' ', h('strong', {}, `${left.length} left.`)),
    h('article', { class: 'hero triage' },
      h('div', { class: 'hero-top' }, projChip(t.project), h('span', {}, `touched ${ago(t.updated_at)}`), duePill(due),
        t.priority && t.priority <= 2 && h('span', { class: `prio${t.priority}` }, PRIORITY[t.priority])),
      h('a', { class: 'hero-title', href: `#/task/${t.id}` }, shortTitle(t)),
      t.body && h('div', { class: 'md triage-body', html: markdown(t.body.slice(0, 600)) }),
      t.last_note && h('p', { class: 'meta' }, `Last note, ${t.last_note.author}: ${t.last_note.text.slice(0, 160)}`),
      h('div', { class: 'hero-actions' }, actions.map(([label, key, fn, cls]) =>
        h('button', { class: cls || '', onclick: fn, title: `Key: ${key}` }, label)))),
    h('p', { class: 'muted keys' }, 'Keys: ', actions.map(([label, key]) => h('span', {}, h('kbd', {}, key), ` ${label.toLowerCase()}  `))),
  ];
}
let triageKeys = {};

const liveIn = (project) => state.live.filter((s) => s.project === project && s.state !== 'done');

async function viewSearch(q) {
  const res = await api(`/api/tasks?status=all&q=${encodeURIComponent(q)}&limit=200`).catch((e) => { showError(e); return { tasks: [] }; });
  const open = res.tasks.filter((t) => t.status === 'todo' || t.status === 'doing');
  const closed = res.tasks.filter((t) => !open.includes(t));
  return [
    section(`Open matching “${q}”`, open, { always: true, empty: 'No open task matches.' }),
    closed.length && h('section', { class: 'group closed' }, h('h2', {}, 'Closed', h('span', { class: 'n' }, closed.length)),
      h('ul', { class: 'tasks' }, closed.slice(0, 50).map((t) => h('li', { class: 'task closed' },
        h('button', { class: 'check on', title: 'Reopen', onclick: () => save(t.id, { status: 'todo' }).then(() => route()) }),
        h('a', { class: 'body', href: `#/task/${t.id}` }, h('span', { class: 'title' }, shortTitle(t)),
          h('span', { class: 'meta' }, projChip(t.project), h('span', {}, `${t.status} ${ago(t.closed_at || t.updated_at)}`))))))),
  ];
}

async function viewTask(id) {
  let t;
  try {
    t = await api(`/api/tasks/${id}`);
  } catch (e) {
    return h('p', { class: 'muted' }, e.message === 'not found' ? 'No such task.' : e.message);
  }
  const field = (name, input) => h('label', { class: 'field' }, h('span', {}, name), input);
  const status = h('div', { class: 'seg' }, ['todo', 'doing', 'done', 'dropped'].map((s) =>
    h('button', { class: t.status === s ? 'on' : '', onclick: async () => { await save(t.id, { status: s }); route(); } }, s)));
  const title = h('textarea', { class: 'edit-title', rows: 1, 'aria-label': 'Title' });
  title.value = t.title;
  title.addEventListener('change', () => save(t.id, { title: title.value }));
  title.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); title.blur(); } });
  const prio = h('select', { onchange: (e) => save(t.id, { priority: e.target.value ? Number(e.target.value) : null }) },
    h('option', { value: '' }, '—'), [1, 2, 3, 4].map((p) => h('option', { value: p, selected: t.priority === p }, PRIORITY[p])));
  const dueDay = h('input', { type: 'date', value: t.due ? t.due.slice(0, 10) : '' });
  const dueTime = h('input', { type: 'time', value: t.due && t.due.length > 10 ? t.due.slice(11) : '' });
  const saveDue = () => save(t.id, { due: dueDay.value ? dueDay.value + (dueTime.value ? 'T' + dueTime.value : '') : null });
  dueDay.addEventListener('change', saveDue);
  dueTime.addEventListener('change', saveDue);
  const project = h('input', { list: 'project-list', value: t.project, spellcheck: 'false' });
  project.addEventListener('change', () => project.value.trim() && save(t.id, { project: project.value.trim() }));
  const tags = h('input', { value: t.tags.join(', '), placeholder: 'claude, marketing', spellcheck: 'false' });
  tags.addEventListener('change', () => save(t.id, { tags: tags.value.split(',').map((x) => x.trim()).filter(Boolean) }));

  const rendered = h('div', { class: 'md', html: markdown(t.body) || '<p class="muted">No details. Click Edit to add some.</p>' });
  let media = taskMedia(t.body);
  const editor = h('textarea', { class: 'edit-body', rows: 12, hidden: true });
  editor.value = t.body;
  const editBtn = h('button', { onclick: async () => {
    if (editor.hidden) { editor.hidden = false; rendered.hidden = true; editBtn.textContent = 'Save'; editor.focus(); return; }
    const saved = await save(t.id, { body: editor.value });
    if (saved) {
      const fresh = taskMedia(saved.body);
      media.replaceWith(fresh); media = fresh;
      rendered.innerHTML = markdown(saved.body); editor.hidden = true; rendered.hidden = false; editBtn.textContent = 'Edit';
    }
  } }, 'Edit');

  const comment = h('textarea', { rows: 2, placeholder: 'Add a note' });
  const comments = h('ul', { class: 'comments' }, t.comments.map(noteItem));
  const notesCount = h('span', { class: 'n' }, t.comments.length);
  const addComment = h('button', { onclick: async () => {
    if (!comment.value.trim()) return;
    try {
      const c = await api(`/api/tasks/${t.id}/comments`, { text: comment.value });
      comments.append(noteItem(c));
      notesCount.textContent = comments.children.length;
      comment.value = '';
    } catch (e) { showError(e); }
  } }, 'Add note');

  let agent = agentBar(t), sessions = sessionsSection(t), live = liveCard(t);
  // While the agent has it, its state, sessions and notes follow along (the rest of the page
  // is left alone, so a half-typed edit survives).
  clearInterval(taskPoll);
  // a run working it without the agent tag (an answer carried out on its own) is followed live too
  if (withAgent(t) || t.sessions.some((s) => s.state)) taskPoll = setInterval(async () => {
    if (document.hidden || current().view !== 'task' || current().arg !== String(t.id)) return;
    const fresh = await api(`/api/tasks/${t.id}`).catch(() => null);
    if (!fresh) return;
    const a = agentBar(fresh), s = sessionsSection(fresh) || h('span', { hidden: true });
    const l = liveCard(fresh);
    agent.replaceWith(a); sessions.replaceWith(s); live.replaceWith(l); agent = a; sessions = s; live = l;
    if (fresh.comments.length !== comments.children.length) {
      comments.replaceChildren(...fresh.comments.map(noteItem));
      notesCount.textContent = fresh.comments.length;
    }
  }, 15000);

  setTimeout(() => autoGrow(title), 0);
  return [
    h('p', { class: 'crumb' }, h('a', { href: '#/now' }, '← Today'), ' · ', h('a', { href: `#/backlog/${encodeURIComponent(t.project)}` }, t.project), ` · #${t.id}`),
    h('article', { class: 'detail' },
      title,
      live,
      h('div', { class: 'fields' },
        field('Status', status), field('Priority', prio), field('Due', h('span', { class: 'due-in' }, dueDay, dueTime)),
        field('Project', project), field('Tags', tags)),
      agent,
      h('p', { class: 'meta' }, `created ${ago(t.created_at)} · updated ${ago(t.updated_at)}`,
        t.source ? ' · ' : '', sourceLink(t.source)),
      media,
      h('div', { class: 'body-head' }, h('h2', {}, 'Details'), editBtn),
      rendered, editor),
    sessions || h('span', { hidden: true }),
    taskDecisions(t.id),
    h('section', { class: 'group' }, h('h2', {}, 'Notes', notesCount), comments,
      h('div', { class: 'add-note' }, comment, addComment)),
  ];
}

// Pictures, videos and sounds named in the details show on the task itself, the way a decision card shows
// them (2026-11-24: "why don't I see the screenshot image?"): pictures as a slideshow a tap opens full size,
// videos and sounds play in place.
const MEDIA_URL = /https?:\/\/[^\s<>"'()]+|\/api\/questions\/file\/[\w.-]+/g;
const AUDIO_EXT = /\.(mp3|m4a|wav|ogg|oga|opus|aac|flac)$/i, VIDEO_EXT = /\.(mp4|webm|mov|m4v)$/i;
const IMAGE_EXT = /\.(png|jpe?g|webp|gif|avif)$/i;
function taskMedia(body) {
  const pics = [], videos = [], sounds = [];
  for (const raw of new Set(String(body || '').match(MEDIA_URL) || [])) {
    const url = raw.replace(/[.,;:!?]+$/, '');
    let path;
    try { path = new URL(url, location.href).pathname; } catch { continue; }
    if (AUDIO_EXT.test(path)) sounds.push(url);
    else if (VIDEO_EXT.test(path)) videos.push(url);
    else if (IMAGE_EXT.test(path) || path.startsWith('/api/questions/file/')) pics.push({ picture: url });
  }
  if (!pics.length && !videos.length && !sounds.length) return h('span', { hidden: true });
  return h('div', { class: 'task-media' },
    pics.length > 0 && h('div', { class: 'see-pics' }, slideshow(pics)),
    ...videos.map((src) => h('video', { class: 'task-video', controls: true, preload: 'metadata', playsinline: true, src })),
    ...sounds.map((src) => h('div', { class: 'see-audio' }, h('audio', { controls: true, preload: 'metadata', src }))));
}

// The decisions this task asked for, open ones answerable right here, the rest one line each.
function taskDecisions(id) {
  const box = h('section', { class: 'group', hidden: true });
  // answering refreshes this section only: the rest of the page may hold a half-typed note or edit
  const fill = () => api(`/api/questions?status=all&limit=50&task=${id}`).then(({ questions }) => {
    if (!questions.length) return;
    const open = questions.filter((q) => q.status === 'open'), done = questions.filter((q) => q.status !== 'open');
    box.replaceChildren(h('h2', {}, 'Decisions ', h('span', { class: 'n' }, questions.length)),
      ...open.map((q) => qCard(q, { onChange: fill, href: `/decisions#/q/${q.id}` })),
      done.length > 0 && h('ul', { class: 'linked-list' }, done.map((q) => h('li', {},
        h('a', { href: `/decisions#/q/${q.id}` }, q.question),
        h('span', { class: 'muted' }, q.status === 'answered' ? ` · ✓ ${q.answer}` : ' · no longer needed')))));
    box.hidden = false;
  }).catch(() => {});
  fill();
  return box;
}

let taskPoll;

const noteItem = (c) => h('li', {}, h('span', { class: 'meta' }, `${c.author} · ${ago(c.at)}`), h('div', { class: 'md', html: markdown(c.text) }));

function agentBar(t) {
  const handed = withAgent(t);
  const closed = t.status === 'done' || t.status === 'dropped';
  // a run carrying out his answer is working it: the live card says so, no hand-off offer under it
  if ((closed || liveRun(t)) && !handed) return h('span', { hidden: true });
  const st = handed ? agentStatus(listed(t), t.sessions.filter((s) => s.state)) : null;
  const flip = h('button', { class: handed ? '' : 'primary', onclick: async () => {
    flip.disabled = true;
    await (handed ? takeBack(listed(t)) : handToAgent(listed(t)));
    route();
  } }, handed ? 'Take back' : 'Hand to agent');
  return h('div', { class: `agent-bar${handed ? ' on' : ''}` },
    handed ? agentLine(st) : h('span', { class: 'muted' }, `Yours. Hand it to the agent and it works it on ${AGENT_HOME}: a branch and a PR, or a note when it needs you.`),
    !closed && flip);
}

function sessionsSection(t) {
  if (!t.sessions.length) return null;
  return h('section', { class: 'group' }, h('h2', {}, 'Agent sessions'),
    h('ul', { class: 'tasks' }, t.sessions.map((s) => h('li', { class: 'agent' },
      h('span', { class: `state ${s.state || 'closed'}` }),
      h('a', { class: 'body', href: `/usage#/session/${encodeURIComponent(s.session_id)}` }, h('span', { class: 'title' }, s.title || 'Agent run'),
        h('span', { class: 'meta' }, h('span', {}, s.state ? `${s.state} on ${s.machine}` : 'closed'), s.doing && h('span', {}, plainStep(s.doing).label)))))));
}

// A step the collector sent, in plain words with an icon: never a raw command, path or link.
/** @type {[RegExp, string, string][]} */
const COMMANDS = [
  [/\b(unittest|pytest|ruff|tsc|npm (run )?(test|check|lint)|scripts\/check)\b/, '🧪', 'Running the checks'],
  [/\bshot\b|screenshot/, '📸', 'Taking a screenshot'],
  [/\bgit (commit|push)\b/, '📦', 'Saving the work'],
  [/\bgit\b/, '🌿', 'Looking at the code history'],
  [/\btasks\b/, '🗂️', 'Updating the task'],
  [/\bssh\b|systemctl/, '🖥️', 'Working on a server'],
  [/\bcurl\b/, '🌐', 'Asking a service'],
  [/./, '🔍', 'Looking through the code'],
];
/** @type {[RegExp, string][]} */
const SAID_ICONS = [
  [/screenshot|shot\b/i, '📸'], [/\btests?\b|check/i, '🧪'], [/commit|push|merge/i, '📦'], [/deploy|restart|live/i, '🚀'],
];
function plainStep(text, said = false) {
  const t = String(text || '').trim();
  if (said) return { icon: '💬', label: t, said: true };
  if (t.startsWith('$ ')) { const [, icon, label] = COMMANDS.find(([re]) => re.test(t)); return { icon, label }; }
  if (t === 'Reading your message') return { icon: '📨', label: 'Reading your answer' };
  if (/^(Editing|Writing) /.test(t)) return { icon: '✏️', label: 'Changing the code' };
  if (/^Reading \S+\.\w{1,5}$|^Reading a file$/.test(t)) return { icon: '📖', label: 'Reading the code' };
  if (/^Reading /.test(t)) return { icon: '🌐', label: 'Reading a web page' };
  if (/^Searching the web/.test(t)) return { icon: '🌐', label: 'Searching the web' };
  if (/^(Searching for|Looking for) /.test(t)) return { icon: '🔍', label: 'Looking through the code' };
  if (/^Delegating/.test(t)) return { icon: '🤝', label: 'Handing a part to a helper' };
  if (/^Planning/.test(t)) return { icon: '🗒️', label: 'Planning the next steps' };
  if (/^Using /.test(t)) return { icon: '🧰', label: t };
  // the agent's own words for a command (its description)
  const hit = SAID_ICONS.find(([re]) => re.test(t));
  return { icon: hit ? hit[1] : '⚙️', label: t };
}

// The run working this task right now, on top of the page: which step of the plan it is on, what it does
// this moment in plain words, and what it did before (each kind once, with a count), so a run nobody
// started by hand can be watched as it works.
const liveRun = (t) => t.sessions.filter((x) => x.state === 'working').sort((a, b) => (b.last || 0) - (a.last || 0))[0];
function liveCard(t) {
  const s = liveRun(t);
  if (!s) return h('span', { hidden: true });
  const [now, ...before] = [...(s.steps || [])].reverse().map((x) => ({ ...plainStep(x.text, x.said), at: x.at }));
  const head = now || plainStep(s.doing || 'Working');
  const past = [];
  for (const x of before) {
    const seen = past.find((p) => p.label === x.label);
    if (seen) seen.n += 1; else past.push({ ...x, n: 1 });
  }
  const step = t.step && t.plan && t.plan[t.step - 1];
  return h('section', { class: 'live-run' },
    h('div', { class: 'live-head' }, h('span', { class: 'pulse' }), h('b', {}, 'Working on it now'),
      step && h('span', { class: 'live-where' }, `Step ${t.step} of ${t.plan.length}`)),
    step && h('p', { class: 'live-plan' }, step.label),
    h('div', { class: 'live-now' }, h('span', { class: 'live-ico' }, head.icon), h('span', {}, head.label)),
    past.length > 0 && h('ol', { class: 'live-steps' }, h('li', { class: 'live-before' }, 'Before that'),
      past.slice(0, 4).map((x) => h('li', {},
        h('span', { class: 'live-ico' }, x.icon), h('span', { class: 'live-label' }, x.label),
        x.n > 1 && h('span', { class: 'live-n' }, `×${x.n}`),
        x.at && h('span', { class: 'live-ago' }, ago(x.at))))));
}

// ------------------------------------------------------------------ routing + render

function current() {
  const [, view, ...rest] = (location.hash || '#/now').slice(1).split('/');
  return { view: view || 'now', arg: decodeURIComponent(rest.join('/')) };
}

let renderSeq = 0;
async function render() {
  const { view, arg } = current();
  for (const a of document.querySelectorAll('#tabs a')) a.classList.toggle('on', /** @type {HTMLElement} */ (a).dataset.view === view);
  document.body.classList.toggle('on-task', view === 'task' || view === 'triage');
  const stale = state.tasks.filter((t) => isStale(t) && !withAgent(t) && !agentAsks(t)).length;
  $('stale-count').textContent = stale ? String(stale) : '';
  document.title = 'Beacon · Tasks';
  if (view !== 'triage') triageKeys = {};
  if (!state.loaded && !['task', 'search'].includes(view)) return;
  const seq = ++renderSeq;
  let nodes;
  if (view === 'backlog') nodes = viewBacklog(arg);
  else if (view === 'triage') nodes = viewTriage(arg);
  else if (view === 'task') nodes = await viewTask(Number(arg));
  else if (view === 'search') nodes = await viewSearch(arg);
  else nodes = viewHome();
  if (seq !== renderSeq) return;  // a newer render started while this one waited
  $('view').replaceChildren(...[nodes].flat(3).filter(Boolean));
}

const OLD_LINKS = { projects: '#/backlog', project: '#/backlog/', stale: '#/triage', now: null };

function route() {
  const old = current();
  if (OLD_LINKS[old.view] !== undefined && OLD_LINKS[old.view]) {
    history.replaceState(null, '', OLD_LINKS[old.view] + (old.view === 'project' ? encodeURIComponent(old.arg) : ''));
  }
  const { view, arg } = current();
  if (view === 'search') {
    if (document.activeElement !== $('search')) /** @type {HTMLInputElement} */ ($('search')).value = arg;
  } else /** @type {HTMLInputElement} */ ($('search')).value = '';
  if (view === 'new') { history.replaceState(null, '', '#/now'); $('capture-text').focus(); }
  window.scrollTo(0, 0);
  render();
}

function renderProjects() {
  $('project-list').replaceChildren(...state.projects.map((p) => h('option', { value: p.project })));
}

// ------------------------------------------------------------------ capture

function autoGrow(el) {
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 320)}px`;
}

function remembered(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode: just not remembered */ }
}

// One line does it all: "Call the plumber #house-cleanup !1 tomorrow 14:00". The words for
// project, priority and day come out of the title and show as chips while he types.
// Full day names only: "po", "ne", "so", "sun" are everyday words in a title.
const WEEKDAYS = [['sunday', 'neděle', 'nedele', 'v neděli'], ['monday', 'mon', 'pondělí', 'pondeli'], ['tuesday', 'tue', 'úterý', 'utery'],
  ['wednesday', 'wed', 'středa', 'streda', 've středu'], ['thursday', 'thu', 'čtvrtek', 'ctvrtek'], ['friday', 'fri', 'pátek', 'patek'],
  ['saturday', 'sobota', 'v sobotu']];
const DAY_WORDS = { today: 0, dnes: 0, tomorrow: 1, tmrw: 1, zítra: 1, zitra: 1, 'next week': 7, 'příští týden': 7, 'pristi tyden': 7 };

function dayFor(word) {
  const w = word.toLowerCase();
  if (w in DAY_WORDS) return pragueToday(DAY_WORDS[w]);
  const wd = WEEKDAYS.findIndex((names) => names.includes(w));
  if (wd >= 0) {
    const now = new Date(pragueToday() + 'T12:00Z').getUTCDay();
    return pragueToday(((wd - now + 7) % 7) || 7);
  }
  let m = w.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return w;
  m = w.match(/^(\d{1,2})\.\s?(\d{1,2})\.(\d{4})?$/);  // 24.12. or 24.12.2026
  if (m && +m[1] >= 1 && +m[1] <= 31 && +m[2] >= 1 && +m[2] <= 12) {
    const today = pragueToday();
    let iso = `${m[3] || today.slice(0, 4)}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`;
    if (!m[3] && iso < today) iso = `${Number(today.slice(0, 4)) + 1}${iso.slice(4)}`;
    return iso;
  }
  return null;
}

function parseCapture(text) {
  const [first, ...rest] = text.split('\n');
  const out = { project: null, priority: null, day: null, time: null };
  let title = ' ' + first + ' ';
  // two-word day phrases first, then single tokens
  title = title.replace(/\s(next week|příští týden|pristi tyden|v neděli|v nedeli|v sobotu|ve středu|ve stredu)(?=\s)/i, (all, w) => { out.day = dayFor(w); return ' '; });
  title = title.replace(/\s(\S+)(?=\s)/g, (all, w) => {
    let m;
    if ((m = w.match(/^#([\w.-]+)$/u)) && !out.project) {
      const known = state.projects.find((p) => p.project.toLowerCase() === m[1].toLowerCase());
      out.project = known ? known.project : m[1];
      return ' ';
    }
    if ((m = w.match(/^!([1-4])$/)) && !out.priority) { out.priority = Number(m[1]); return ' '; }
    if (/^\d{1,2}:\d{2}$/.test(w) && !out.time) { out.time = w.padStart(5, '0'); return ' '; }
    const d = !out.day && dayFor(w);
    if (d) { out.day = d; return ' '; }
    return all;
  });
  out.text = [title.replace(/\s+/g, ' ').trim(), ...rest].join('\n').trim();
  out.project = out.project || (current().view === 'backlog' && current().arg) || null;
  return out;
}

function renderParsed() {
  const text = /** @type {HTMLInputElement} */ ($('capture-text')).value;
  const p = parseCapture(text);
  const due = p.day && dueLabel(p.day + (p.time ? 'T' + p.time : ''));
  $('capture-chips').replaceChildren(...(text.trim() ? [
    h('span', { class: 'cchip' }, 'in ', h('b', {}, p.project || 'personal')),
    due && h('span', { class: 'cchip' }, 'due ', h('b', {}, due.text)),
    p.priority && h('span', { class: `cchip prio${p.priority}` }, h('b', {}, PRIORITY[p.priority])),
  ] : [h('span', { class: 'hint' }, 'Type, then add ', h('code', {}, '#project'), ' ', h('code', {}, '!1'), ' ', h('code', {}, 'tomorrow'), ' if you like')]).filter(Boolean));
}

async function capture(e) {
  e.preventDefault();
  const p = parseCapture(/** @type {HTMLInputElement} */ ($('capture-text')).value.trim());
  if (!p.text) return;
  const body = { body: p.text, project: p.project || undefined };
  if (p.priority) body.priority = p.priority;
  if (p.day) body.due = p.day + (p.time ? 'T' + p.time : '');
  const btn = e.target.querySelector('button[type=submit]');
  btn.disabled = true;
  try {
    const t = listed(await api('/api/tasks', body));
    /** @type {HTMLInputElement} */ ($('capture-text')).value = '';
    autoGrow($('capture-text'));
    renderParsed();
    state.tasks.unshift(t);
    if (!state.projects.some((x) => x.project === t.project)) {
      state.projects.push({ project: t.project, open: 1 });
      renderProjects();
    }
    toast(`Added to ${t.project}: ${t.title}`, () => save(t.id, { status: 'dropped' }));
    showError(null);
    render();
  } catch (err) {
    showError(err);
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ boot

$('capture').addEventListener('submit', capture);
$('capture-text').addEventListener('input', (e) => { autoGrow(e.target); renderParsed(); });
$('capture-text').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); /** @type {HTMLFormElement} */ ($('capture')).requestSubmit(); }
});
renderParsed();

let searchTimer;
$('search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  const q = /** @type {HTMLInputElement} */ (e.target).value.trim();
  searchTimer = setTimeout(() => {
    const hash = q ? `#/search/${encodeURIComponent(q)}` : '#/now';
    if (location.hash === hash) render(); else location.hash = hash;
  }, 250);
});
$('search').addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && /** @type {HTMLInputElement} */ ($('search')).value) { e.stopPropagation(); /** @type {HTMLInputElement} */ ($('search')).value = ''; location.hash = '#/now'; }
});
document.addEventListener('keydown', (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
  if (e.key === 'Escape') { closeMenu(); if (typing) /** @type {HTMLElement} */ (document.activeElement).blur(); return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === '/') { e.preventDefault(); $('search').focus(); }
  if (e.key === 'n' && current().view !== 'triage') { e.preventDefault(); $('capture-text').focus(); return; }
  if (triageKeys[e.key]) { e.preventDefault(); triageKeys[e.key](); }
});

window.addEventListener('hashchange', route);
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
setInterval(() => { if (!document.hidden && !document.activeElement?.matches('textarea, input')) load(); }, 30000);
if (location.pathname === '/new') { history.replaceState(null, '', '/#/now'); setTimeout(() => $('capture-text').focus(), 0); }
// paint the last snapshot at once (a few seconds old at most), then the fresh data
const last = snap.get('tasks');
if (last) { try { take(last); renderProjects(); } catch { /* an old shape: wait for fresh data */ } }
route();
load();
