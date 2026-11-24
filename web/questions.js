// Decisions (http://beacon/decisions): everything the agents need him to decide, on one page, and every decision he made
// (hub/questions.py). Builds on shared.js. Views by hash: #/ (open), #/q/<id>, #/log, #/log/<kind>, #/learned[/<rule id>] (#/rules still works), #/finished.
import { $, ago, api, h, markdown, setDecisionsBadge, showError, snap, toast } from './shared.js';
import { projChip } from './names.js';
import { projectName, qCard } from './qcards.js';
import './viewer.js';

const state = { open: [], endings: [], gone: [], loaded: false };

// ------------------------------------------------------------------ load + answer

async function load() {
  clearTimeout(loadSoon);
  const started = Date.now();
  try {
    const [qs, es] = await Promise.all([api('/api/questions?status=open&limit=500'),
      api('/api/endings').catch(() => ({ endings: state.endings }))]);
    // A list fetched after an answer landed is the truth; an older one still holds cards he already answered.
    for (const [id, s] of sent) if (s.done && s.done < started) sent.delete(id);
    state.open = qs.questions.filter((q) => !sent.has(q.id));
    state.endings = es.endings || [];
    state.gone = state.gone.filter((id) => state.endings.some((e) => e.id === id));
    snap.set('decisions', state.open);
    snap.set('endings', state.endings);
    state.loaded = true;
    noteFresh();
    showError(null);
  } catch (e) {
    showError(e);
  }
  const n = state.open.length;
  $('open-count').textContent = n ? String(n) : '';
  if (state.loaded) setDecisionsBadge(n);
  document.title = n ? `(${n}) Beacon · Decisions` : 'Beacon · Decisions';
  // Never rebuild under a half-typed answer; the log only changes when he answers.
  // Only a real change repaints (a repaint of the same card would deal it in again: 2026-11-22 "blinks twice").
  const sig = fullSig();
  const changed = sig !== state.sig;
  state.sig = sig;
  if (!changed || !['open', 'finished'].includes(current().view)) return;
  if (deck.dragging || deck.busy || (document.activeElement?.matches('.answer-own input') && /** @type {HTMLInputElement} */ (document.activeElement).value)) { state.sig = null; return; }
  render();
}
// What the deck shows of each question: a new one, a reworded one or a new screenshot repaints, nothing else.
const sigOf = (qs) => qs.map((q) => `${q.id}:${q.updated_at || ''}:${q.blocking ? 1 : 0}:${(q.see || []).length}`).join(',');
// The open list changed here (an answer, a dismissal): the hub's next answer is then no change.
// The feed's cards change the page too (task 1857): one signature for both.
const endSig = (es) => (es || []).map((e) => `${e.id}:${e.updated_at}:${e.open_question || ''}`).join(',');
const fullSig = () => `${sigOf(state.open)}|${endSig(state.endings)}`;
function dropOpen(id) { state.open = state.open.filter((x) => x.id !== id); state.sig = fullSig(); }

// The card itself (answer, dismiss, project names): qcards.js.
// On its own page (#/q/<id>) an answer shows as decided at once; the deck repaints from load().
const card = (q, focus) => qCard(q, { focus, onChange: () => { load(); if (current().view === 'q') render(); } });

// ------------------------------------------------------------------ history

function decision(q) {
  const gone = q.status === 'withdrawn';
  return h('li', { class: 'decision' },
    h('div', { class: 'q' }, h('a', { href: `#/q/${q.id}` }, q.question)),
    h('div', { class: 'ans' }, h('span', { class: `said${gone ? ' gone' : ''}` }, gone ? 'No longer needed' : q.answer),
      q.task_id && h('a', { class: 'when', href: `/tasks#/task/${q.task_id}` }, `task #${q.task_id}`),
      q.rule && h('a', { class: 'by-rule', href: `#/learned/${q.rule.id}` }, `🤖 Answered by rule: ${q.rule.summary}`),
      h('span', { class: 'when' }, `${projectName(q.project)} · ${ago(q.answered_at || q.updated_at)}`)),
    q.answer_note && !gone && h('div', { class: 'note' }, ruleNote(q.answer_note)));
}

// ------------------------------------------------------------------ the feed: every session's ending (task 1857)
// One pile (2026-11-23, ?255): a needs (❓) or stuck (⚠️) reply is a card in the deck (the hub files it); finished (✅)
// replies are a log behind one link (#/finished). A button sends the agent's suggested next step back into that
// session (hub/endings.py → answer-dispatcher); "Got it" clears the card, with undo.
const feed = { open: false };
const PILL = { needs: 'needs you', stuck: 'not yet', done: 'done' };
const DONE_SHOWN = 6;

function endingsShown() {
  return (state.endings || []).filter((e) => !e.open_question && !(state.gone || []).includes(e.id));
}

function flyOut(el, then) {
  el.style.transition = 'transform .25s ease-in, opacity .25s ease-in';
  el.style.transform = 'translateX(70px)';
  el.style.opacity = '0';
  setTimeout(then, 230);
}

function clearEnding(e, el) {
  flyOut(el, async () => {
    state.gone = [...(state.gone || []), e.id];
    render();
    try {
      await api(`/api/endings/${e.id}/clear`, {});
      toast(e.state === 'done' ? 'Got it' : 'Dismissed', async () => {
        try { await api(`/api/endings/${e.id}/unclear`, {}); } catch (err) { showError(err); }
        state.gone = (state.gone || []).filter((x) => x !== e.id);
        load();
      });
    } catch (err) {
      state.gone = (state.gone || []).filter((x) => x !== e.id);
      showError(err);
      render();
    }
  });
}

// A button, or his own words typed on the card (2026-11-22: "it'd be nice if I can also reply there").
function actEnding(e, label, el, words) {
  el.querySelectorAll('button, input').forEach((b) => { b.disabled = true; });
  flyOut(el, async () => {
    state.gone = [...(state.gone || []), e.id];
    render();
    try {
      await api(`/api/endings/${e.id}/act`, words ? { words } : { label });
      toast(`Sent ${words ? 'your reply' : `„${label}“`} to the session. Its new card lands here once it ran.`);
    } catch (err) {
      state.gone = (state.gone || []).filter((x) => x !== e.id);
      showError(err);
      render();
    }
  });
}

function endingCard(e) {
  const name = e.title || projectName(e.project);
  const where = [e.title && e.project && projectName(e.project), e.machine, ago(e.updated_at)].filter(Boolean).join(' · ');
  const el = h('article', { class: `ecard ${e.state}` });
  const btns = e.actions.map((label, i) => h('button', { class: `ebtn${i === 0 ? ' go' : ''}`, onclick: () => actEnding(e, label, el) }, label));
  btns.push(h('button', { class: 'ebtn quiet', onclick: () => clearEnding(e, el) }, e.state === 'done' ? 'Got it' : 'Dismiss'));
  const own = h('input', { placeholder: 'Reply to this session', 'aria-label': 'Your reply', enterkeyhint: 'send' });
  const reply = () => own.value.trim() && actEnding(e, null, el, own.value.trim());
  const send = h('button', { class: 'send', 'aria-label': 'Send', disabled: true, onclick: reply }, '→');
  own.addEventListener('input', () => { send.disabled = !own.value.trim(); });
  own.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && !ev.isComposing) { ev.preventDefault(); reply(); } });
  el.append(...[  // the DOM's own append writes a null as "null": only real parts
    h('div', { class: 'etop' }, h('span', { class: 'epill' }, PILL[e.state]), h('span', { class: 'ename' }, name)),
    h('p', { class: 'eline' }, e.line),
    e.next && h('p', { class: 'enext' }, h('span', { 'aria-hidden': 'true' }, '👉 '), e.next),
    h('div', { class: 'ebtns' }, ...btns),
    h('div', { class: 'answer-own' }, own, send),
    h('p', { class: 'ewhere' }, where)].filter(Boolean));
  return el;
}

// A ⚠️ "not yet" reply with no buttons of its own is a status, not a decision: it sits here too (2026-11-23).
const finished = () => endingsShown().filter((e) => e.state !== 'needs');

// Under the deck only one quiet line: what finished lately is a log he opens, never a pile to scroll (2026-11-23, ?255).
function finishedLink() {
  const n = finished().length;
  return n ? h('p', { class: 'hub-links' }, h('a', { href: '#/finished' }, `✅ ${n} finished lately →`)) : null;
}

function viewFinished() {
  const done = finished();
  const clearAll = h('button', { class: 'ebtn quiet small', onclick: async () => {
    const ids = done.map((e) => e.id);
    state.gone = [...(state.gone || []), ...ids];
    render();
    try {
      await api('/api/endings/clear', { ids });
      toast(`Cleared ${ids.length} finished`, async () => {
        try { await api('/api/endings/unclear', { ids }); } catch (err) { showError(err); }
        state.gone = state.gone.filter((x) => !ids.includes(x));
        load();
      });
    } catch (err) { showError(err); }
    load();
  } }, 'Clear all');
  const shown = feed.open ? done : done.slice(0, DONE_SHOWN);
  return [h('p', { class: 'crumb' }, h('a', { href: '#/' }, '← Decisions')),
    h('section', { class: 'feed' }, h('div', { class: 'fgroup' },
      h('div', { class: 'fhead' }, h('h2', {}, '✅ Finished lately', h('span', { class: 'n' }, String(done.length))), done.length > 1 && clearAll),
      done.length ? shown.map(endingCard) : h('p', { class: 'muted' }, 'Nothing finished lately.'),
      done.length > shown.length && h('button', { class: 'ebtn quiet more', onclick: () => { feed.open = true; render(); } },
        `Show ${done.length - shown.length} more`)))];
}

// ------------------------------------------------------------------ views

// The deck (2026-11-22): one card at a time instead of a long list, every question in one pile,
// newest first, no sections. Swipes and ← → only move between cards, like photos: only a tapped button answers
// (he swiped to browse and sent 4 answers by mistake, 2026-11-22). Every answer gets the toast's undo.
const deck = { cur: null, filter: 'all', dragging: false, busy: false, done: 0, skipped: new Set(), fresh: new Set(), primed: false };
// New decisions while he is on another card: a pill over the deck jumps to them (hub.js does it on the other pages).
const SEEN_KEY = 'hub.decisionsSeen';
const seenId = () => { try { return Number(localStorage.getItem(SEEN_KEY)) || 0; } catch { return 0; } };
function markSeen(id) { if (id > seenId()) try { localStorage.setItem(SEEN_KEY, String(id)); } catch { /* blocked */ } }
function noteFresh() {
  const top = Math.max(0, ...state.open.map((q) => q.id));
  if (!deck.primed) {  // the page just opened: the deck starts on the newest, or on the card the pill named
    deck.primed = true;
    const want = Number(new URLSearchParams(location.search).get('new'));
    if (want) { deck.cur = want; deck.skipped.delete(want); history.replaceState(null, '', location.pathname + location.hash); state.sig = null; }
    markSeen(top);
    return;
  }
  const seen = seenId();
  for (const q of state.open) if (q.id > seen) deck.fresh.add(q.id);
  for (const id of deck.fresh) if (!state.open.some((q) => q.id === id)) deck.fresh.delete(id);
}
function jumpFresh() {
  const newest = [...deck.fresh].sort((a, b) => b - a)[0];
  deck.fresh.clear();
  deck.skipped.delete(newest);
  deck.filter = 'all';
  deck.cur = newest;
  render();
}
const LATER_KEY = 'questions-later';
const later = () => { try { return JSON.parse(localStorage.getItem(LATER_KEY)) || []; } catch { return []; } };
function setLater(ids) { try { localStorage.setItem(LATER_KEY, JSON.stringify(ids.slice(-200))); } catch { /* private window */ } }

function pile() {
  const l = later();
  const qs = state.open.filter((q) => deck.filter === 'all' || q.blocking)
    .sort((a, b) => b.created_at - a.created_at || b.id - a.id);
  // "Later" sends a card to the bottom of the pile, in the order he put them off.
  return [...qs.filter((q) => !l.includes(q.id)), ...l.map((id) => qs.find((q) => q.id === id)).filter(Boolean)];
}

function putOff(q) {
  deck.skipped.add(q.id);
  setLater([...later().filter((id) => id !== q.id), q.id]);
  const next = nextAfter(q);
  deck.cur = next ? next.id : null;
  render();
}

// A card flies off in 150 ms; a key, tap or swipe meanwhile lands it at once instead of being dropped
// (2026-11-22: "takes so long and doesn't update on every key").
let flying = null;
function fly(el, dir, then) {
  settle();
  deck.busy = true;
  const x = dir === 'up' ? 0 : (dir === 'right' ? 1 : -1) * (window.innerWidth + 200);
  const y = dir === 'up' ? -window.innerHeight : 40;
  el.style.pointerEvents = 'none';
  el.style.transition = 'transform .15s ease-in, opacity .15s ease-in';
  el.style.transform = `translate(${x}px, ${y}px) rotate(${dir === 'up' ? 0 : dir === 'right' ? 18 : -18}deg)`;
  el.style.opacity = '0';
  flying = { then, timer: setTimeout(settle, 150) };
}
function settle() {
  if (!flying) return;
  const f = flying;
  flying = null;
  clearTimeout(f.timer);
  deck.busy = false;
  f.then();
}

// The card after q: the next one not put off yet, else the deck ends (viewOpen shows the end).
function nextAfter(q) {
  const p = pile(), i = p.findIndex((x) => x.id === q.id);
  return [...p.slice(i + 1), ...p.slice(0, Math.max(i, 0))].find((x) => !deck.skipped.has(x.id));
}

// What this page sent and the hub may not list as gone yet: id → { done: when the hub confirmed }.
// The card leaves and the toast shows at once; the hub is told behind it, one refresh after the last answer.
const sent = new Map();
let loadSoon = 0;
const refreshSoon = () => { clearTimeout(loadSoon); loadSoon = setTimeout(load, 1500); };
function send(q, path, body, label, back) {
  const s = { done: null };
  sent.set(q.id, s);
  const comeBack = () => {
    sent.delete(q.id);
    state.open = [q, ...state.open.filter((x) => x.id !== q.id)];
    back();
    state.sig = fullSig();
    if (current().view === 'open') render();
    refreshSoon();
  };
  const req = api(path, body).then(() => { s.done = Date.now(); return true; },
    (e) => { showError(e); comeBack(); return false; });  // not sent: the card comes back instead of vanishing
  toast(label, async () => {
    if (!(await req)) return;
    try { await api(`/api/questions/${q.id}/reopen`, {}); } catch (e) { return showError(e); }
    comeBack();
  });
  req.then(refreshSoon);
}

function deckAnswer(q, text, el) {
  settle();
  if (sent.has(q.id)) return;  // a second tap or key on a card already on its way
  const own = el.querySelector('.answer-own input')?.value.trim();
  const note = own && own !== text ? own : null;
  const next = nextAfter(q);
  deck.done += 1;
  send(q, `/api/questions/${q.id}/answer`, { answer: text, note }, `Sent: ${text}`,
    () => { deck.cur = q.id; deck.done = Math.max(0, deck.done - 1); });
  dropOpen(q.id);
  fly(el, 'up', () => { deck.cur = next ? next.id : null; render(); });
}

// Sent back with a 👎 and his words (already answered by the flag call): the card just leaves.
function deckGone(q, el) {
  settle();
  const next = nextAfter(q);
  deck.done += 1;
  dropOpen(q.id);
  fly(el, 'up', () => { deck.cur = next ? next.id : null; render(); });
}

function deckDismiss(q, el) {
  settle();
  if (sent.has(q.id)) return;
  const next = nextAfter(q);
  send(q, `/api/questions/${q.id}/withdraw`, { note: 'dismissed by him: not needed' }, 'Removed: not needed',
    () => { deck.cur = q.id; });
  dropOpen(q.id);
  fly(el, 'up', () => { deck.cur = next ? next.id : null; render(); });
}

// One way in for keys and the round buttons: lands a flying card first, then acts on the card now shown.
function deckAct(what) {
  if (current().view !== 'open') return;
  settle();
  const c = document.querySelector('.deck-card');
  const p = pile(), i = p.findIndex((x) => x.id === deck.cur);
  if (!c || i < 0) return;
  if (what === 'later') fly(c, 'left', () => putOff(p[i]));
  else if (what === 'dismiss') deckDismiss(p[i], c);
  else if (p.length > 1) {
    const d = what === 'next' ? 1 : -1;
    fly(c, d > 0 ? 'left' : 'right', () => { deck.cur = p[(i + d + p.length) % p.length].id; render(); });
  }
}

// Drag sideways to move between cards (left = next, right = previous); vertical moves stay page scroll.
function makeSwipeable(el, go) {
  const stamp = el.querySelector('.stamp');
  let x0 = null, y0 = 0, dx = 0, id = null, moved = false;
  el.addEventListener('pointerdown', (e) => {
    if (deck.busy) settle();
    if (e.target.closest('input, textarea, summary, .see-track, .see-nav, .see-dots, .qcontext blockquote') || e.button > 0) return;
    x0 = e.clientX; y0 = e.clientY; dx = 0; id = e.pointerId; moved = false;
  });
  el.addEventListener('pointermove', (e) => {
    if (x0 === null || e.pointerId !== id) return;
    dx = e.clientX - x0;
    if (!moved) {
      if (Math.abs(e.clientY - y0) > 12 && Math.abs(dx) < 12) { x0 = null; return; }  // he is scrolling
      if (Math.abs(dx) < 12) return;
      moved = true; deck.dragging = true; el.setPointerCapture(id); el.classList.add('dragging');
    }
    el.style.transform = `translateX(${dx}px) rotate(${dx / 30}deg)`;
    stamp.textContent = dx < 0 ? 'Next →' : '← Previous';
    stamp.className = `stamp ${dx > 0 ? 'right' : 'left'}`;
    stamp.style.opacity = Math.min(1, Math.abs(dx) / 110);
  });
  const end = () => {
    if (x0 === null) return;
    x0 = null; deck.dragging = false; el.classList.remove('dragging');
    if (!moved) return;
    // A drag that started on a button must not also click it.
    el.addEventListener('click', (e) => { e.stopPropagation(); e.preventDefault(); }, { capture: true, once: true });
    if (Math.abs(dx) > Math.min(110, el.offsetWidth * 0.3)) fly(el, dx > 0 ? 'right' : 'left', () => go(dx > 0 ? -1 : 1));
    else { el.style.transition = 'transform .2s ease'; el.style.transform = ''; stamp.style.opacity = 0;
      setTimeout(() => { el.style.transition = ''; }, 200); }
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
}

function viewOpen() {
  if (!state.loaded) return h('p', { class: 'muted' }, 'Loading…');
  const waiting = state.open.filter((q) => q.blocking).length;
  if (deck.filter === 'waiting' && !waiting) deck.filter = 'all';
  const p = pile();
  const rest = finishedLink();
  if (!p.length) {
    if (rest) return [h('p', { class: 'calm' }, h('span', { class: 'tick', 'aria-hidden': 'true' }, '✓'),
      deck.done ? `No decisions left, ${deck.done} answered` : 'No decisions waiting'), rest];
    return h('section', { class: 'allclear' }, h('div', { class: 'tick', 'aria-hidden': 'true' }, '✓'),
      h('h2', {}, deck.done ? `All clear, ${deck.done} answered` : 'All clear'), h('p', {}, 'Nothing needs you right now.'));
  }
  if (!p.some((x) => !deck.skipped.has(x.id))) {
    const again = h('button', { class: 'ebtn', onclick: () => { deck.skipped.clear(); deck.cur = null; render(); } }, `Go through the ${p.length} again`);
    return [h('section', { class: 'allclear deck-end' }, h('div', { class: 'tick', 'aria-hidden': 'true' }, '✓'),
      h('h2', {}, 'You went through them all'),
      h('p', {}, `${deck.done ? `${deck.done} answered · ` : ''}${p.length} put off for later`), again), rest];
  }
  let i = p.findIndex((q) => q.id === deck.cur);
  if (i < 0) i = p.findIndex((q) => !deck.skipped.has(q.id));
  const q = p[i];
  deck.cur = q.id;
  deck.fresh.delete(q.id);
  const fresh = p.filter((x) => deck.fresh.has(x.id));
  if (!fresh.length) markSeen(Math.max(0, ...state.open.map((x) => x.id)));
  const freshPill = fresh.length > 0 && h('button', { class: 'new-here', onclick: jumpFresh },
    h('span', { class: 'dot', 'aria-hidden': 'true' }),
    fresh.length === 1 ? `New: ${fresh[0].question}` : `${fresh.length} new decisions · show the newest`);
  const chip = (key, label, n) => h('button', { class: `chip${deck.filter === key ? ' on' : ''}`,
    onclick: () => { deck.filter = key; deck.cur = null; render(); } }, label, h('span', { class: 'n' }, String(n)));
  const c = qCard(q, { onChange: load, foot: false, onAnswer: (text) => deckAnswer(q, text, c), onGone: () => deckGone(q, c) });
  c.classList.add('deck-card');
  if (deck.shown !== q.id) c.classList.add('deal');  // the same card repainted stays still
  deck.shown = q.id;
  c.prepend(h('div', { class: 'stamp', 'aria-hidden': 'true' }));
  const go = (d) => { deck.cur = p[(i + d + p.length) % p.length].id; render(); };
  makeSwipeable(c, go);
  const total = p.length + deck.done;
  return [
    freshPill,
    h('div', { class: 'deck-bar' },
      h('div', { class: 'chips' }, chip('all', 'All', state.open.length), waiting > 0 && chip('waiting', 'Agent waiting', waiting)),
      h('span', { class: 'deck-count' }, `${i + 1} of ${p.length}`)),
    h('div', { class: 'deck-progress', 'aria-hidden': 'true' }, h('span', { style: `width:${Math.round((deck.done / total) * 100)}%` })),
    h('div', { class: 'deck' }, p.length > 2 && h('div', { class: 'ghost g2' }), p.length > 1 && h('div', { class: 'ghost g1' }), c),
    h('div', { class: 'deck-actions' },
      h('button', { class: 'round', 'aria-label': 'Previous', title: 'Previous (←)', disabled: p.length < 2, onclick: () => deckAct('prev') }, '↩'),
      h('button', { class: 'round', title: 'Not needed', onclick: () => deckAct('dismiss') }, h('span', {}, '🗑'), h('small', {}, 'Not needed')),
      h('button', { class: 'round', title: 'Later (↓)', onclick: () => deckAct('later') }, h('span', {}, '⏭'), h('small', {}, 'Later')),
      h('button', { class: 'round', 'aria-label': 'Next', title: 'Next (→)', disabled: p.length < 2, onclick: () => deckAct('next') }, '↪')),
    h('p', { class: 'deck-tip' }, '← → to browse · press 1, 2, 3 or click to answer · just type for your own'),
    rest,
  ];
}

async function viewOne(id) {
  let q;
  try {
    q = await api(`/api/questions/${id}`);
  } catch (e) {
    return h('p', { class: 'muted' }, e.message === 'not found' ? 'No such decision.' : e.message);
  }
  const links = whereFrom(q);
  if (q.status === 'open') return [h('p', { class: 'crumb' }, h('a', { href: '#/' }, '← All decisions')), card(q, false), links];
  // Decided: one line with what he picked and whether it was carried out, never the options again
  // (they read like a third choice under the answer).
  const gone = q.status === 'withdrawn';
  return [h('p', { class: 'crumb' }, h('a', { href: '#/log' }, '← History')),
    h('article', { class: 'qcard' },
      h('div', { class: 'qtop' }, projChip(q.project), h('span', {}, `asked ${ago(q.created_at)}`)),
      h('h3', { class: 'qtext' }, q.question),
      h('p', { class: `decided${gone ? ' gone' : ''}` }, gone ? 'No longer needed' : `✓ Decided: ${q.answer}`,
        h('span', { class: 'muted' }, ` · ${ago(q.answered_at || q.updated_at)}`)),
      q.rule && !gone && h('p', {}, h('a', { class: 'by-rule', href: `#/learned/${q.rule.id}` }, `🤖 Answered by rule: ${q.rule.summary} →`)),
      q.answer_note && !gone && h('p', { class: 'note' }, ruleNote(q.answer_note)),
      !gone && h('p', { class: 'muted carried' }, q.applied_at ? `Carried out · the agent took it ${ago(q.applied_at)}` : 'Waiting for the agent to take it'),
      !gone && !q.applied_at && h('button', { class: 'skip', onclick: async () => {
        try { await api(`/api/questions/${q.id}/reopen`, {}); render(); load(); } catch (e) { showError(e); }
      } }, 'Change my answer'),
      q.context && h('details', { class: 'qmore' }, h('summary', {}, 'Details'), h('div', { class: 'md qcontext', html: markdown(q.context) }))),
    links];
}

// Where a decision came from: its task and the session that asked, each one tap away.
function whereFrom(q) {
  const box = h('p', { class: 'hub-links' });
  const parts = [];
  if (q.session_id) parts.push(h('a', { href: `/usage#/session/${encodeURIComponent(q.session_id)}` }, 'Session that asked →'));
  if (q.task_id) {
    const a = h('a', { href: `/tasks#/task/${q.task_id}` }, `Task #${q.task_id} →`);
    parts.push(a);
    api(`/api/tasks/${q.task_id}`).then((t) => { a.textContent = `${t.title} →`; }).catch(() => {});
  }
  parts.push(h('a', { href: `/tasks#/backlog/${encodeURIComponent(q.project)}` }, `${projectName(q.project)} tasks →`));
  box.replaceChildren(...parts);
  return box;
}

// The log loads once (about 1000 rows) and search filters it here, on every key, the way the hub's
// own search does (store.fold: lowercase, no accents; question, context, answer, note).
const fold = (t) => String(t || '').normalize('NFKD').toLowerCase().replace(/\p{M}/gu, '');
let logCache = null;
async function logRows() {
  if (logCache && Date.now() - logCache.at < 60000) return logCache.rows;
  const rows = (await api('/api/questions?status=answered,withdrawn&limit=1000')).questions;
  for (const q of rows) q._text = fold([q.question, q.context, q.answer, q.answer_note].join('\n'));
  logCache = { rows, at: Date.now() };
  return rows;
}

async function viewLog(kind, search) {
  let rows;
  try {
    rows = await logRows();
  } catch (e) {
    return h('p', { class: 'error' }, e.message);
  }
  if (kind) rows = rows.filter((q) => q.kind === kind);
  // He decided only what he answered: a card an agent withdrew shows up only when searched for (2026-11-22).
  if (!search) rows = rows.filter((q) => q.status !== 'withdrawn');
  if (search) { const needle = fold(search); rows = rows.filter((q) => q._text.includes(needle)); }
  return h('section', { class: 'group' },
    h('div', { class: 'group-head' }, h('h2', {}, search ? `Matching “${search}”` : 'What you decided'),
      h('p', {}, search ? `${rows.length} found` : 'Newest first. Agents read this before they ask again.')),
    rows.length ? h('ul', { class: 'history' }, rows.slice(0, 300).map(decision)) : h('p', { class: 'muted' }, 'Nothing here yet.'));
}

// ------------------------------------------------------------------ learned (hub/rules.py)
// What the agents learned from his answers (task 1824): the week-by-week share they answered for him,
// every rule in plain words with the answers it came from, how often it answered, an on/off switch,
// and the kinds he still answers himself.

const day = (t) => (t ? new Date(t * 1000).toLocaleDateString(window.hubLocale || 'en-GB', { day: 'numeric', month: 'short' }) : '');
const weekDay = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(window.hubLocale || 'en-GB', { day: 'numeric', month: 'short' });
const RULE_STATUS = { proposed: 'Waiting for your yes', declined: 'You said no' };
const ruleNote = (note) => (note || '').replace(/^Pravidlo #\d+:\s*(.)/, (_, c) => `Why: ${c.toUpperCase()}`);  // the hub's note names the rule by number

async function ruleAction(path, body) {
  try { await api(path, body); render(); } catch (e) { showError(e); }
}

// One stacked bar per week: you (blue) on top of what a rule answered for you (orange).
function weekChart(weeks) {
  const shown = weeks.slice(-12);
  const max = Math.max(1, ...shown.map((w) => w.him + w.auto));
  const pct = (w) => (w.him + w.auto ? Math.round((w.auto / (w.him + w.auto)) * 100) : 0);
  return h('figure', { class: 'wchart' },
    h('div', { class: 'wlegend' },
      h('span', {}, h('i', { class: 'sw him' }), 'You answered'),
      h('span', {}, h('i', { class: 'sw auto' }), 'A rule answered for you')),
    h('div', { class: 'wbars', role: 'img', 'aria-label': shown.map((w) => `${weekDay(w.week)}: you ${w.him}, rules ${w.auto}`).join('; ') },
      shown.map((w) => {
        const tip = `Week of ${weekDay(w.week)}: you ${w.him}, rules ${w.auto}${w.him + w.auto ? ` (${pct(w)}% for you)` : ''}`;
        return h('div', { class: 'wcol', title: tip, tabindex: '0', 'aria-label': tip },
          h('span', { class: 'wtop' }, String(w.him + w.auto || '')),
          h('div', { class: 'wstack', style: `height:${((w.him + w.auto) / max) * 100}%` },
            w.him > 0 && h('i', { class: 'him', style: `flex:${w.him}` }),
            w.auto > 0 && h('i', { class: 'auto', style: `flex:${w.auto}` })),
          h('span', { class: 'wlab' }, weekDay(w.week)));
      })),
    h('figcaption', {}, 'Questions per week, Monday to Sunday. Orange = answered by a rule; hover a week for the share.'));
}

function ruleSwitch(r) {
  const on = r.status === 'active';
  return h('button', { class: `switch${on ? ' on' : ''}`, role: 'switch', 'aria-checked': String(on),
    title: on ? 'On: it answers matching questions for you. Tap to turn off.' : 'Off: you get asked. Tap to turn on.',
    onclick: () => ruleAction(`/api/rules/${r.id}`, { status: on ? 'off' : 'active' }) },
  h('span', { class: 'knob' }), h('span', { class: 'slabel' }, on ? 'On' : 'Off'));
}

function ruleCard(r, focus) {
  const edit = h('div', { class: 'rule-edit', hidden: true },
    h('input', { value: r.summary, 'aria-label': 'Rule' }), h('input', { value: r.answer, 'aria-label': 'Answer it gives' }),
    h('button', { class: 'opt pick', onclick: () => {
      const [s, a] = edit.querySelectorAll('input');
      ruleAction(`/api/rules/${r.id}`, { summary: s.value.trim(), answer: a.value.trim() });
    } }, 'Save'));
  const live = ['active', 'off'].includes(r.status);
  const used = r.answered
    ? `answered for you · last ${ago(r.last_fired_at)}${r.taken_back ? ` · ${r.taken_back} taken back` : ''}`
    : r.taken_back ? `answered · ${r.taken_back} taken back` : 'not used yet';
  const onAt = r.source === 'his-words' ? r.created_at : r.decided_at;
  const from = (r.source === 'his-words' ? 'You said it in your own words' : `Learned from these ${r.evidence_questions.length} answers of yours`)
    + (['active', 'off'].includes(r.status) && onAt ? ` · you turned it on ${day(onAt)}` : '')
    + (r.status === 'declined' && r.decided_at ? ` · you said no ${day(r.decided_at)}` : '');
  return h('article', { class: `qcard rule ${r.status}${focus ? ' focus' : ''}`, id: `rule-${r.id}` },
    h('div', { class: 'rule-head' },
      h('div', {},
        h('div', { class: 'qtop' }, r.project ? projChip(r.project) : h('span', { class: 'proj-chip' }, 'All projects'), h('span', {}, r.kind)),
        h('h3', { class: 'qtext' }, r.summary)),
      live ? ruleSwitch(r) : h('span', { class: 'wait' }, RULE_STATUS[r.status] || r.status)),
    h('p', { class: 'rule-gives' }, 'Answers: ', h('b', {}, r.answer)),
    h('div', { class: 'rule-stats' },
      h('span', { class: `big${r.answered ? '' : ' zero'}` }, String(r.answered)),
      h('span', {}, used)),
    h('div', { class: 'rule-from' }, h('p', { class: 'muted' }, from),
      h('ul', { class: 'history' }, r.evidence_questions.map((q) => h('li', { class: 'decision' },
        h('div', { class: 'q' }, h('a', { href: `#/q/${q.id}` }, q.question)),
        h('div', { class: 'ans' }, h('span', { class: 'said' }, q.answer), h('span', { class: 'when' }, day(q.answered_at))))))),
    r.uses.length > 0 && h('details', { class: 'more-info', open: !!focus }, h('summary', {}, 'What it answered for you'),
      h('ul', { class: 'history' }, r.uses.map((u) => h('li', { class: 'decision' },
        h('div', { class: 'q' }, h('a', { href: `#/q/${u.question_id}` }, u.question)),
        h('div', { class: 'ans' }, h('span', { class: `said${u.undone_at ? ' gone' : ''}` }, u.undone_at ? 'Taken back' : u.answer),
          h('span', { class: 'when' }, day(u.at)),
          !u.undone_at && h('button', { class: 'skip', onclick: () => ruleAction(`/api/rules/uses/${u.id}/undo`, {}) }, 'Take back')),
        h('div', { class: 'note' }, `Why: ${u.why}`))))),
    edit,
    h('div', { class: 'qfoot' },
      h('button', { class: 'skip', onclick: () => { edit.hidden = !edit.hidden; } }, 'Edit the wording'),
      r.status === 'proposed' && r.proposal_qid && h('a', { class: 'skip', href: `#/q/${r.proposal_qid}` }, 'Answer the proposal')));
}

// The kinds of question he still answers himself (last 30 days): where the next rule could come from.
function stillAsked(kinds) {
  const top = kinds.filter((k) => !['rule', 'test'].includes(k.kind)).slice(0, 6);
  if (!top.length) return null;
  const max = Math.max(...top.map((k) => k.answered));
  return h('section', { class: 'group still' },
    h('div', { class: 'group-head' }, h('h2', {}, 'Still asked to you'),
      h('p', {}, 'Last 30 days, by kind of question. Kinds where you mostly take the suggestion are the next rules.')),
    h('div', { class: 'kbars' }, h('div', { class: 'wlegend' },
      h('span', {}, h('i', { class: 'sw took' }), 'You took the agent’s suggestion'),
      h('span', {}, h('i', { class: 'sw him' }), 'You answered your own way')),
    h('ul', {}, top.map((k) => {
      const tip = `${k.answered} asked, you took the agent’s suggestion ${k.took_default}×`;
      return h('li', { title: tip },
        h('span', { class: 'kname' }, k.kind.replace(/-/g, ' ')),
        h('span', { class: 'kbar', 'aria-label': tip },
          h('i', { class: 'took', style: `width:${(k.took_default / max) * 100}%` }),
          h('i', { class: 'him', style: `width:${((k.answered - k.took_default) / max) * 100}%` })),
        h('span', { class: 'kn' }, String(k.answered)));
    }))));
}

// The evening review (23:00 on the home server): one line on what it checked last time and what came of it.
function sweepLine(s) {
  if (!s) return h('p', { class: 'sweep-line' }, '🌙 The nightly review runs at 23:00: it reads your answers from the last 30 days. Its first run shows here.');
  const late = Date.now() / 1000 - s.at > 36 * 3600;
  const when = new Date(s.at * 1000).toLocaleString(window.hubLocale || 'en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  const found = [s.repeated ? `${s.repeated} ${s.repeated === 1 ? 'kind' : 'kinds'} you answered the same way 3×` : 'nothing you answered the same way 3×',
    s.risky && `${s.risky} left to you (money, sending, people…)`, s.covered && `${s.covered} already a rule`].filter(Boolean).join(', ');
  return h('div', { class: `sweep-line${late ? ' late' : ''}` },
    h('p', {}, `${late ? '⚠️ No nightly review since' : '🌙 Nightly review'} ${when}: read ${s.answers} answers from the last ${s.days} days, found ${found}.`),
    s.proposed.length
      ? h('p', {}, `Proposed ${s.proposed.length === 1 ? 'a rule' : `${s.proposed.length} rules`} for your yes: `,
        ...s.proposed.map((p, i) => [i ? ', ' : '', h('a', { href: `#/learned/${p.id}` }, p.summary)]).flat())
      : h('p', { class: 'muted' }, 'Nothing new to propose.'));
}

// Every question a rule answered for him, newest first, each with a way to take it back.
function decidedForYou(rows) {
  return h('section', { class: 'group for-you' },
    h('div', { class: 'group-head' }, h('h2', {}, 'Decided for you'),
      h('p', {}, rows.length ? 'Newest first. Take one back and the question comes to you again (if the agent hasn’t acted on it yet).' : '')),
    rows.length ? h('ul', { class: 'history' }, rows.map((u) => h('li', { class: 'decision' },
      h('div', { class: 'q' }, h('a', { href: `#/q/${u.question_id}` }, u.question)),
      h('div', { class: 'ans' }, h('span', { class: `said${u.undone_at ? ' gone' : ''}` }, u.undone_at ? 'Taken back' : u.answer),
        h('span', { class: 'when' }, day(u.at)),
        !u.undone_at && h('button', { class: 'skip', onclick: () => ruleAction(`/api/rules/uses/${u.id}/undo`, {}) }, 'Take back')),
      h('div', { class: 'note' }, '🤖 ', h('a', { href: `#/learned/${u.rule_id}` }, u.summary), ` · Why: ${u.why}`))))
      : h('p', { class: 'muted lempty' }, 'Nothing yet: no rule has answered a question for you.'));
}

async function viewLearned(focusId) {
  let data, kinds = [];
  try {
    [data, kinds] = await Promise.all([api('/api/rules'),
      api(`/api/questions/kinds?since=${Math.floor(Date.now() / 1000) - 30 * 86400}`).then((x) => x.kinds).catch(() => [])]);
  } catch (e) {
    return h('p', { class: 'error' }, e.message);
  }
  const { rules, weeks, decided = [], last_sweep: sweep = null } = data;
  const w = weeks[weeks.length - 1] || { him: 0, auto: 0 };
  const total = weeks.reduce((n, x) => n + x.auto, 0);
  const on = rules.filter((r) => r.status === 'active').length;
  const share = w.him + w.auto ? Math.round((w.auto / (w.him + w.auto)) * 100) : 0;
  const shown = rules.filter((r) => r.status !== 'declined'), declined = rules.filter((r) => r.status === 'declined');
  const out = [
    h('section', { class: 'group' },
      h('div', { class: 'group-head' }, h('h2', {}, 'What the agents learned from you'),
        h('p', {}, 'Same answer twice → you get asked if it should be a rule. Money, sending, publishing, deleting, going live and people always come to you.')),
      sweepLine(sweep),
      h('div', { class: 'ltiles' },
        h('div', { class: 'ltile' }, h('b', {}, `${share}%`), h('span', {}, 'answered for you this week')),
        h('div', { class: 'ltile' }, h('b', {}, String(total)), h('span', {}, total === 1 ? 'question you never had to answer' : 'questions you never had to answer')),
        h('div', { class: 'ltile' }, h('b', {}, String(on)), h('span', {}, on === 1 ? 'rule on' : 'rules on'))),
      weekChart(weeks)),
    decidedForYou(decided),
    h('section', { class: 'group' },
      h('div', { class: 'group-head' }, h('h2', {}, 'Rules'), h('p', {}, shown.length ? 'Switch one off and its kind of question comes back to you.' : '')),
      shown.length ? shown.map((r) => ruleCard(r, r.id === focusId))
        : h('p', { class: 'muted' }, 'No rules yet. The first one shows up here when you answer the same kind of question the same way twice.'),
      declined.length > 0 && h('details', { class: 'more-info' }, h('summary', {}, `Rules you said no to (${declined.length})`),
        declined.map((r) => ruleCard(r, r.id === focusId)))),
    stillAsked(kinds),
  ];
  if (focusId) setTimeout(() => document.getElementById(`rule-${focusId}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 50);
  return out;
}

// ------------------------------------------------------------------ routing + render

function current() {
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  return { view: parts[0] || 'open', arg: decodeURIComponent(parts.slice(1).join('/')) };
}

let renderSeq = 0;
let renderWasInLog = false;
async function render() {
  const { view, arg } = current();
  for (const a of $('tabs').querySelectorAll('a')) a.classList.toggle('on', a.dataset.view === ({ q: 'open', search: 'log', rules: 'learned' }[view] || view));
  const seq = ++renderSeq;
  const inLog = ['log', 'search'].includes(view);
  if (inLog && !renderWasInLog) logCache = null;  // coming back to the log: fresh rows, then every key filters those
  renderWasInLog = inLog;
  let out;
  if (view === 'q') out = await viewOne(Number(arg));
  else if (view === 'log') out = await viewLog(arg, null);
  else if (view === 'search') out = await viewLog(null, arg);
  else if (view === 'learned' || view === 'rules') out = await viewLearned(Number(arg) || null);
  else if (view === 'finished') out = viewFinished();
  else out = viewOpen();
  if (seq !== renderSeq) return;  // a newer render started while this one waited on the hub
  $('view').replaceChildren(...[out].flat().filter(Boolean));
  fitDeck();
  focusOwn();
}

// On a computer the card's own-answer box always has the cursor (2026-11-22), so he types an answer at once;
// a number key alone in the empty box still picks that option (after a short wait, so a typed number stays). Not on a phone: it would pop the keyboard up.
const keyboard = matchMedia('(hover: hover) and (pointer: fine)');
function focusOwn() {
  if (!keyboard.matches || document.activeElement?.matches('input:not(.answer-own input), textarea')) return;
  /** @type {HTMLElement | null} */ (document.querySelector('.deck-card .answer-own input'))?.focus({ preventScroll: true });
}

// A computer screen shows the whole card without scrolling (2026-11-22: "I still have to scroll, super
// bad"): the pictures' column is as tall as the taller of the words and the answers, so it never makes the card longer.
const wide = matchMedia('(min-width: 1000px)');
function fitDeck() {
  const c = /** @type {HTMLElement | null} */ (document.querySelector('.deck-card')), pics = /** @type {HTMLElement | null | undefined} */ (c?.querySelector('.see-pics'));
  if (!pics || !wide.matches) { c?.style.removeProperty('--pic-h'); return; }
  const tall = Math.max(/** @type {HTMLElement} */ (c.querySelector('.qmain')).offsetHeight, /** @type {HTMLElement} */ (c.querySelector('.qside')).offsetHeight);
  const extra = pics.offsetHeight - (/** @type {HTMLElement | null} */ (pics.querySelector('.see-pic'))?.offsetHeight || 0);  // the slideshow dots
  c.style.setProperty('--pic-h', `${Math.round(Math.min(620, Math.max(240, tall - extra)))}px`);
}
addEventListener('resize', fitDeck);
document.addEventListener('load', (e) => { if (/** @type {Element} */ (e.target).closest?.('.deck-card')) fitDeck(); }, true);

// ------------------------------------------------------------------ boot

// Search as he types: the list follows each key (a short pause so a fast typist sees one repaint, not ten).
let searchT = 0;
function searchNow() {
  clearTimeout(searchT);
  const v = /** @type {HTMLInputElement} */ ($('search')).value.trim();
  const hash = v ? `#/search/${encodeURIComponent(v)}` : '#/log';
  if (location.hash === hash) return;
  const { view, arg } = current();
  if (view === 'search' || (view === 'log' && !arg)) {
    history.replaceState(null, '', hash);  // no hashchange: one history step for the whole search
    render();
  } else location.hash = hash;
}
$('search').addEventListener('input', () => { clearTimeout(searchT); searchT = setTimeout(searchNow, 120); });
$('search').addEventListener('keydown', (e) => { if (e.key === 'Enter') searchNow(); });
document.addEventListener('keydown', (e) => {
  // His own-answer box is focused on a computer (focusOwn): while it is empty, the deck keys still work there.
  const ownEmpty = /** @type {HTMLInputElement} */ (e.target).matches('.deck-card .answer-own input') && !/** @type {HTMLInputElement} */ (e.target).value;
  if (/** @type {HTMLInputElement} */ (e.target).matches('input, textarea') && !ownEmpty) { if (e.key === 'Escape') /** @type {HTMLInputElement} */ (e.target).blur(); return; }
  if (e.key === '/') { e.preventDefault(); $('search').focus(); }
  // The deck by keyboard: ← → move between cards, ↓ later, 1-9 picks that answer (2026-11-22: "number
  // shortcuts", to clear the deck fast). Arrows never answer; a number is as deliberate as a tap.
  if (current().view !== 'open' || !document.querySelector('.deck-card')) return;
  const nav = { ArrowRight: 'next', ArrowLeft: 'prev', ArrowDown: 'later' }[e.key];
  if (nav) { e.preventDefault(); return deckAct(nav); }
  if (/^[1-9]$/.test(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey) {
    settle();  // a number pressed while the last card flies off picks on the card now shown
    const opt = /** @type {HTMLElement} */ (document.querySelectorAll('.deck-card .opts .opt')[Number(e.key) - 1]);
    if (!opt) return;
    // In his empty answer box a number waits a moment (2026-11-23: "if I continue typing, it counts as actual
    // answer"): the digit is typed, and only when nothing follows within NUMBER_WAIT does it pick that option.
    if (ownEmpty) { armNumber(e.target, e.key, opt); return; }
    e.preventDefault();
    if (!e.repeat) opt.click();  // a held key answers once
  }
});
const NUMBER_WAIT = 550;
let armed = null;
function disarm() {
  if (!armed) return;
  clearTimeout(armed.timer);
  armed.opt.classList.remove('arming');
  armed = null;
}
function armNumber(input, key, opt) {
  disarm();
  opt.classList.add('arming');
  armed = { opt, timer: setTimeout(() => {
    const go = input.isConnected && input.value === key;
    disarm();
    if (go) { input.value = ''; opt.click(); }
  }, NUMBER_WAIT) };
}
document.addEventListener('input', (e) => { if (armed && /** @type {HTMLInputElement} */ (e.target).matches('.deck-card .answer-own input') && /** @type {HTMLInputElement} */ (e.target).value.length !== 1) disarm(); });
window.addEventListener('hashchange', render);
// paint the last snapshot at once, then the fresh list
const last = snap.get('decisions');
const lastEnds = snap.get('endings');
if (Array.isArray(lastEnds)) state.endings = lastEnds;
if (Array.isArray(last)) { state.open = last; state.loaded = true; state.sig = fullSig(); $('open-count').textContent = last.length ? String(last.length) : ''; render(); }
load();
// the log loads its own rows; one card's own page fetches its card (blank on a phone with no snapshot yet, 2026-11-23)
if (current().view !== 'open' && !(current().view === 'q' && Array.isArray(last))) render();
// Fresh questions arrive on their own; not while he types an answer.
setInterval(() => { if (!document.hidden && !/** @type {HTMLInputElement | null} */ (document.activeElement)?.value) load(); }, 15000);
