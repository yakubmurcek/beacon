// A question card, answerable in one tap: on the Decisions page and on the Tasks home screen
// ("Decide"). Builds on shared.js. `onChange` runs after an answer, an undo or a dismissal.
import { ago, api, copyText, h, markdown, showError, toast } from './shared.js';
import { dropProject, pname, projChip } from './names.js';

// The project's own name, the same as on every hub page (names.js): "alex.example", never "Alex Example".
export const projectName = (slug) => (!slug || slug === 'personal' ? 'Personal' : pname(slug));
const optionLabel = (o) => o.label.replace(/\s*\(recommended\)\s*$/i, '');
const LONG_CONTEXT = 700;  // only a long recap folds under "More details"; bullets show by default
const STEPS_CONTEXT = 1400; // a by-hand card shows its why and the text he sends (a mail) open, not folded
const SHORT_TITLE = 100;   // a question this long or shorter is the title as it is

// The card reads like the desktop app (2026-11-22: a wall of bold text sent him back to the session):
// the title is only the question, everything else sits under it as bullets. Agents often pack it all
// into the question, so a long one is split here: the sentence with the "?" is the title, the other
// sentences become bullets, and a "Play → F8 → Resets" chain becomes numbered steps.
// Returns { title, details (markdown), task (the task title, shown small), long (no clean title found) }.
// A period after a lone 1-2 digit number is a Czech date or ordinal ("do 5. 12."), not a sentence end;
// after a name or a long number ("on the PC.", "for 2026.") it is.
const SENTENCE_END = /(?<=[^\d][.?!…]|\d[?!…]|\d{3,}\.|[^\s\d]\d{1,2}\.)\s+(?=[„"'(\p{Lu}\p{N}\p{Extended_Pictographic}])/u;
const LIST_TITLE = 120;    // past this, "Lead in three words: a, b, c?" becomes "Lead in three words?" + bullets

// A long title sheds what reads better under it: a long "(…)" aside, a link, a ": a, b, c?" list.
const cap = (x) => x.charAt(0).toUpperCase() + x.slice(1);
function trimTitle(title, rest) {
  if (title.length <= SHORT_TITLE) return title;
  title = title.replace(/\s*\(([^()]{25,})\)/g, (_, aside) => { rest.push(cap(aside)); return ''; });
  title = title.replace(/\s*(https?:\/\/\S+)/g, (_, url) => { rest.push(`🔗 ${url}`); return ''; });
  const list = title.length > LIST_TITLE && title.match(/^((?:\S+\s+){2,}\S+?):\s+(.+?)(\?)?$/);
  if (list) {
    const items = list[2].split(/,\s+(?:and\s+|a\s+|or\s+|nebo\s+)?/);
    if (items.length >= 3) { rest.unshift(...items.map(cap)); return list[1] + (list[3] || ''); }
  }
  return title.trim();
}
// The words that only say "here come steps": dropped, the numbers say it. Any other lead ("Order:") stays on top.
const STEP_WORDS = /^(otestuj|test|try|steps?|kroky|postup|how to test|vyzkoušej)$/i;
function steps(sentence) {
  const parts = sentence.split(/\s*→\s*/).map((x) => x.trim()).filter(Boolean);
  if (parts.length < 3) return `- ${cue(sentence)}`;
  const lead = parts[0].match(/^(.{2,40}?):\s+(.+)$/);  // "Otestuj: Play" → the first step is "Play"
  if (lead) parts[0] = lead[2];
  // Short clicks in a row are one step ("Play → F8 → Me → Money"); the expected result gets a ✅.
  const merged = [];
  for (const x of parts.map((y) => y.replace(/[.;]$/, ''))) {
    const last = merged[merged.length - 1];
    if (last && x.length <= 22 && last.short) { last.text += ` → ${x}`; } else merged.push({ text: x, short: x.length <= 22 });
  }
  const list = merged.map((x, i) => `${i + 1}. ${RESULT.test(x.text) ? '✅ ' : ''}${cap(x.text)}`).join('\n');
  return lead && !STEP_WORDS.test(lead[1].trim()) ? `\n**${lead[1].trim()}:**\n\n${list}\n` : list;
}
// What should happen at the end of a test: "má se stisknout", "do minuty se objeví", "should open".
const RESULT = /^(má se|mělo by|měl by|měla by|should|expect|you should see|do minuty|do \d+ s|uvidíš|objeví se)/i;
// A bullet that is only "Draft: <link>" or "PR: <link>" gets a 🔗 in front, so it reads as "open this".
const cue = (x) => (/^[^\s:]{1,20}:\s+https?:\/\/\S+\s*\.?$/.test(x) ? `🔗 ${x.replace(/\s+\.$/, '')}` : x);
// A recap written as one paragraph of prose is a wall on a phone: each sentence becomes a bullet, a
// "→" chain numbered steps. Paragraphs already in markdown (lists, headings, code, tables) stay as written.
function bulletize(md) {
  return md.split(/\n{2,}/).map((para) => {
    if (/^\s*([-*+]\s|\d+[.)]\s|#|\||```|>)/m.test(para) || para.includes('\n')) return para;
    const sentences = para.split(SENTENCE_END).map((x) => x.trim()).filter(Boolean);
    if (sentences.length < 2 && !/→.*→/.test(para)) return para;
    return sentences.map(steps).join('\n');
  }).join('\n\n');
}
// A bare link reads as its file or host, not 80 characters of address.
function shortLinks(el) {
  for (const a of el.querySelectorAll('a')) {
    const t = a.textContent;
    if (!/^https?:\/\//.test(t) || t.length <= 32) continue;
    try {
      const u = new URL(t), last = u.pathname.split('/').filter(Boolean).pop();
      a.textContent = last ? `${u.hostname.replace(/^www\./, '')} › ${decodeURIComponent(last)}` : u.hostname;
      a.title = t;
    } catch { /* not a URL after all */ }
  }
  return el;
}

// A bullet that only repeats a link already shown as a big button ("🔗 PR: <link>") goes.
function dropShown(el, q) {
  const shown = new Set([...(q.see || []), ...(q.steps || [])].map((x) => x.url).filter(Boolean));
  for (const li of el.querySelectorAll('li')) {
    const a = li.querySelector('a');
    if (a && shown.has(a.href) && li.textContent.replace(a.textContent, '').trim().length <= 20) li.remove();
  }
  return el;
}

function splitQuestion(q) {
  let context = String(q.context || '');
  let task = '';
  context = context.replace(/^Task #\d+:\s*(.*)(\n|$)/, (_, t) => { task = t.trim(); return ''; })
    .replace(/(^|\n)Task: #\d+ ([^\n;]*)[^\n]*/, (_, nl, t) => { task = task || t.trim(); return nl; })
    .replace(/_Asked in the session's next-step box\._/, '')
    .trim();
  const cut = context.search(/\n-{3,}\n|\n?\*\*How to remove\*\*/);
  context = cut < 0 ? bulletize(context) : `${bulletize(context.slice(0, cut).trim())}\n${context.slice(cut)}`;
  const [text, sub] = dropProject(String(q.question || '').trim(), q.project);
  if (text.length <= SHORT_TITLE) return { title: text, details: context, task, long: false, sub };
  const sentences = text.split(SENTENCE_END).map((x) => x.trim()).filter(Boolean);
  let i = sentences.findIndex((x) => /\?["“”]?$/.test(x) && x.length <= 160);
  let title, rest;
  if (i >= 0) {
    // "Can you sign it?" alone says nothing: the sentence before it says what "it" is.
    const vague = i > 0 && sentences[i].length <= 45 && /\b(it|this|that|them|ho|to|je|tam|tohle)\b/i.test(sentences[i]);
    const from = vague ? i - 1 : i;
    title = sentences.slice(from, i + 1).join(' ');
    rest = sentences.filter((_, j) => j < from || j > i);
    // "Team page: where is each project…?": a short lead-in stays, it says what the question is about
  } else {
    const first = sentences[0];
    const lead = first.match(/^(.{2,60}?):\s+(.+)$/);
    if (lead && /→/.test(lead[2])) {  // "Otestuj: Play → F8 → …": the steps are the details
      title = task ? `${lead[1]}: ${task}` : lead[1];
      if (task) task = '';
      rest = [first, ...sentences.slice(1)];
    } else {
      title = first;
      rest = sentences.slice(1);
    }
  }
  title = trimTitle(title, rest);
  const details = [rest.map(steps).join('\n'), context].filter(Boolean).join('\n\n');
  return { title, details, task, long: title.length > 160, sub };
}

// What he looks at or tries before answering (2026-11-22: "I can't decide without seeing it"): pictures
// show on the card, a tap opens them full size; a link is one big button. Above the bullets, so it is seen first.
function hostOf(url) {
  try { const u = new URL(url, location.href); return u.hostname.replace(/^www\./, '') + (u.pathname.length > 1 ? u.pathname : ''); } catch { return url; }
}
// Several pictures are one slideshow (2026-11-22: "slideshow, not below each other"): swipe or tap the
// arrows, a counter says where he is, a tap on the picture opens it full size. Links stay big buttons under it.
export function slideshow(pics) {
  const slide = (x) => h('a', { class: 'see-pic', href: x.picture, target: '_blank', rel: 'noopener' },
    h('img', { src: x.picture, alt: x.label || 'Screenshot', loading: 'lazy', draggable: 'false' }),
    x.label && h('span', { class: 'see-cap' }, x.label));
  if (pics.length === 1) return slide(pics[0]);
  const track = h('div', { class: 'see-track' }, pics.map(slide));
  const count = h('span', { class: 'see-count' }, `1 / ${pics.length}`);
  const dots = h('div', { class: 'see-dots' }, pics.map((_, i) => h('button', { class: i ? '' : 'on', 'aria-label': `Picture ${i + 1}`,
    onclick: () => go(i) })));
  const go = (i) => { i = Math.max(0, Math.min(pics.length - 1, i)); track.scrollTo({ left: i * track.clientWidth, behavior: 'smooth' }); };
  const at = () => Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
  track.addEventListener('scroll', () => {
    const i = at();
    count.textContent = `${i + 1} / ${pics.length}`;
    [...dots.children].forEach((d, j) => d.classList.toggle('on', j === i));
  }, { passive: true });
  return h('div', { class: 'see-show' }, track, count,
    h('button', { class: 'see-nav prev', 'aria-label': 'Previous picture', onclick: () => go(at() - 1) }, '‹'),
    h('button', { class: 'see-nav next', 'aria-label': 'Next picture', onclick: () => go(at() + 1) }, '›'),
    dots);
}
// Pictures are their own block: a computer screen gives them a column of their own, a phone shows them in place.
function seePics(q) {
  const pics = (q.see || []).filter((x) => x.picture);
  return pics.length ? h('div', { class: 'see-pics' }, slideshow(pics)) : null;
}
function seeBlock(q) {
  const see = q.see || [];
  if (!see.length) {
    return q.needs_see ? h('p', { class: 'see-missing' }, '⚠️ No link or picture to look at yet') : null;
  }
  const links = see.filter((x) => x.url);
  // A sound to judge by ear plays right on the card (2026-11-23: "I need the voice actually there to be able to play it")
  const sounds = see.filter((x) => x.audio).map((x) => h('div', { class: 'see-audio' },
    h('span', { class: 'see-go' }, `🎧 ${x.label || 'Listen'}`),
    h('audio', { controls: true, preload: 'metadata', src: x.audio })));
  if (!links.length && !sounds.length) return null;
  return h('div', { class: 'see' }, ...sounds, ...links.map((x) =>
    h('a', { class: 'see-link', href: x.url, target: '_blank', rel: 'noopener' },
      h('span', { class: 'see-go' }, `👀 ${x.label || 'Open it'}`), h('span', { class: 'see-host' }, hostOf(x.url)), h('span', { class: 'see-arrow' }, '↗'))));
}

// A command in a step is a chip he taps to copy (2026-11-23: "I can't copy the terminal command"). Agents
// wrap it in backticks; an older step reading "in Terminal: secrets set KEY, then paste" is found too.
const CMD_LEAD = /^(.*?\b(?:terminal|run|type)\s*:\s*)(.+?)(\s*(?:,\s*(?:then|and)\b|;|\s+then\b).*)?$/i;
function stepParts(text) {
  if (text.includes('`')) return text.split('`').map((s, i) => (i % 2 ? { cmd: s } : s)).filter((x) => x && (x.cmd || x.length));
  const m = text.match(CMD_LEAD);
  return m ? [m[1], { cmd: m[2].trim() }, m[3] || ''].filter((x) => x) : [text];
}
function cmdChip(cmd) {
  const chip = h('button', { class: 'step-cmd', type: 'button', title: 'Copy', onclick: async (ev) => {
    ev.stopPropagation();  // copying is not doing the step: the tick stays his
    const ok = await copyText(cmd);
    chip.classList.toggle('copied', ok);
    toast(ok ? `Copied: ${cmd}` : 'Could not copy: select it by hand');
    setTimeout(() => chip.classList.remove('copied'), 1500);
  } }, h('code', {}, cmd), h('span', { class: 'step-copy' }, 'Copy'));
  return chip;
}

// Text he sends as is (an ad, a mail) is quoted on the card: one tap copies it whole (2026-11-23, ?307: "I can't
// select the text to send or copy it").
function quoteCopy(ctx) {
  for (const quote of ctx.querySelectorAll('blockquote')) {
    const chip = h('button', { class: 'quote-copy', type: 'button', onclick: async (ev) => {
      ev.stopPropagation();
      const ok = await copyText([...quote.children].filter((x) => x !== chip).map((x) => x.innerText).join('\n\n').trim());
      chip.textContent = ok ? 'Copied' : 'Select it by hand';
      setTimeout(() => { chip.textContent = 'Copy text'; }, 1500);
    } }, 'Copy text');
    quote.append(chip);
  }
}

// A card he carries out by hand (2026-11-22: "a direct link and clear steps"): a numbered checklist he
// ticks as he goes; a step with a link is a big button to that exact page. Ticks live on this phone only.
function stepsBlock(q) {
  const steps = q.steps || [];
  if (!steps.length) return null;
  const key = `qsteps:${q.id}`;
  let done = [];
  try { done = JSON.parse(localStorage.getItem(key) || '[]'); } catch { /* private mode: ticks just don't stick */ }
  const count = h('span', { class: 'steps-count' });
  const box = h('ol', { class: 'steps' });
  const paint = () => {
    count.textContent = done.length >= steps.length ? '✅ All done' : `${done.length} of ${steps.length} done`;
    box.closest('.qcard')?.classList.toggle('steps-done', done.length >= steps.length);
  };
  const tick = (i, row) => {
    done = done.includes(i) ? done.filter((x) => x !== i) : [...done, i];
    row.classList.toggle('done', done.includes(i));
    try { localStorage.setItem(key, JSON.stringify(done)); } catch { /* see above */ }
    paint();
  };
  steps.forEach((x, i) => {
    const row = h('li', { class: `step${done.includes(i) ? ' done' : ''}` });
    const mark = h('button', { class: 'step-mark', 'aria-label': `Step ${i + 1} done`, onclick: () => tick(i, row) },
      h('span', { class: 'step-n' }, String(i + 1)), h('span', { class: 'step-ok' }, '✓'));
    // Opening the link counts as doing the step: one tap less on a phone.
    const body = x.url
      ? h('a', { class: 'step-go', title: x.text, href: x.url, target: '_blank', rel: 'noopener', onclick: () => { if (!done.includes(i)) tick(i, row); } },
        h('span', { class: 'step-text' }, x.text.replace(/`/g, '')), h('span', { class: 'see-host' }, hostOf(x.url)), h('span', { class: 'see-arrow' }, '↗'))
      : h('span', { class: 'step-text', onclick: () => tick(i, row) }, stepParts(x.text).map((p) => (p.cmd ? cmdChip(p.cmd) : p)));
    row.append(mark, body);
    box.append(row);
  });
  const el = h('div', { class: 'steps-wrap' }, h('div', { class: 'steps-head' }, h('span', {}, '👉 Do this'), count), box);
  requestAnimationFrame(paint);
  return el;
}

async function answerQuestion(q, text, el, onChange, onUndo) {
  const own = el.querySelector('.answer-own input');
  const note = own && own.value.trim() && own.value.trim() !== text ? own.value.trim() : null;
  try {
    await api(`/api/questions/${q.id}/answer`, { answer: text, note });
    el.classList.add('answered');
    toast(`Sent: ${text}`, async () => {
      try { await api(`/api/questions/${q.id}/reopen`, {}); } catch (e) { showError(e); }
      (onUndo || onChange)();
    });
    onChange();
  } catch (e) {
    showError(e);
    (onUndo || onChange)();  // not sent: the card comes back instead of vanishing
  }
}

async function dismissQuestion(q, onChange) {
  try {
    await api(`/api/questions/${q.id}/withdraw`, { note: 'dismissed by him: not needed' });
    toast('Removed');
    onChange();
  } catch (e) {
    showError(e);
  }
}

// 👎 A card he can't answer as it stands: one tap logs it in the one log of bad cards, which every asking agent is
// shown (2026-11-22), and opens a box right under it for what is wrong (2026-11-23: "ať tam můžu něco připsat").
// The why goes to the asking session's task and teaches every later card a rule (hub/cardrules.py, task 1903). The
// button says so in words: a bare 👎 was never tapped (0 flags in 282 cards). A second tap takes the flag back.
function flagButton(q) {
  const b = h('button', { class: `qflag${q.flagged_at ? ' on' : ''}`,
    title: 'Makes no sense: its agent asks again, and every later card learns from it',
    'aria-label': 'Makes no sense', 'aria-pressed': String(!!q.flagged_at) },
  h('span', { class: 'flag-ico', 'aria-hidden': 'true' }, '👎'), h('span', {}, 'Makes no sense'));
  b.onclick = async (e) => {
    e.stopPropagation();
    const card = b.closest('.qcard');
    try {
      const out = await api(`/api/questions/${q.id}/flag`, {});
      q.flagged_at = out.flagged_at; q.flag = out.flag;
      b.classList.toggle('on', !!q.flagged_at);
      b.setAttribute('aria-pressed', String(!!q.flagged_at));
      card?.querySelectorAll('.qflag-note, .qflag-said').forEach((x) => x.remove());
      if (q.flagged_at && card) {
        const box = flagNote(q);
        card.querySelector('.qtop')?.after(box);
        box.querySelector('input').focus();
      }
      toast(q.flagged_at ? 'Write what is wrong: later cards learn it' : 'Flag taken back');
    } catch (err) { showError(err); }
  };
  return b;
}

function flagNote(q) {
  const input = h('input', { placeholder: 'What is wrong? No later card repeats it', 'aria-label': 'What is wrong',
    enterkeyhint: 'send', maxlength: '500' });
  const send = h('button', { type: 'submit', class: 'send', 'aria-label': 'Send', disabled: true }, '→');
  input.addEventListener('input', () => { send.disabled = !input.value.trim(); });
  const form = h('form', { class: 'qflag-note' }, input, send);
  form.addEventListener('pointerdown', (e) => e.stopPropagation());
  form.onsubmit = async (e) => {
    e.preventDefault();
    const note = input.value.trim();
    if (!note) return;
    send.disabled = true;
    try {
      const out = await api(`/api/questions/${q.id}/flag`, { note });
      q.flagged_at = out.flagged_at; q.flag = out.flag;
      form.replaceWith(h('p', { class: 'qflag-said' }, `👎 ${note}`));
      toast('Sent: its agent asks again, and later cards learn the rule');
    } catch (err) { send.disabled = false; showError(err); }
  };
  return form;
}

// The deck (questions.js) passes `onAnswer` to animate the card away first and `foot: false` (it has its own buttons).
export function qCard(q, { focus = false, onChange = () => {}, href = `#/q/${q.id}`, onAnswer = null, foot = true } = {}) {
  const el = h('article', { class: `qcard${q.steps?.length ? ' todo' : ''}${q.blocking ? ' blocking' : ''}${focus ? ' focus' : ''}`, id: `q${q.id}` });
  const answer = (text) => (onAnswer ? onAnswer(text) : answerQuestion(q, text, el, onChange));
  // A card with steps is a job for his hands, not a question (2026-11-22): it reads as a to-do, and its
  // suggested answer is the ✓ Done button rather than a "Suggested" pick.
  const todo = q.steps?.length > 0;
  // No options (a "test this" card): one button, since a swipe never answers (2026-11-22). A session's own reply
  // with no buttons gets none made up: he answers it in words (2026-11-23).
  const choices = q.options.length ? q.options : q.kind === 'ending' ? [] : [{ label: 'Done, it works' }];
  // On a computer each answer shows its number key (questions.js: 1-9 answers the deck's card).
  const opts = choices.map((o, i) => h('button', {
    class: `opt${o.label === q.default_answer ? ' pick' : ''}`, onclick: () => answer(o.label),
  }, i < 9 && h('kbd', { class: 'opt-key', 'aria-hidden': 'true' }, String(i + 1)), h('span', { class: 'label' }, todo && o.label === q.default_answer && h('span', { class: 'done-mark' }, '✓'), optionLabel(o),
    !todo && o.label === q.default_answer && h('span', { class: 'pick-tag' }, 'Suggested')),
  o.detail && h('span', { class: 'opt-detail' }, o.detail)));
  // Typed text with an option clicked goes along as a note; alone, Enter or the arrow sends it as the answer.
  const own = h('input', { placeholder: q.options.length ? 'Or write your own answer' : 'Write your answer',
    'aria-label': 'Your own answer', enterkeyhint: 'send' });
  const send = h('button', { class: 'send', 'aria-label': 'Send', disabled: true, onclick: () => own.value.trim() && answer(own.value.trim()) }, '→');
  own.addEventListener('input', () => { send.disabled = !own.value.trim(); });
  own.addEventListener('keydown', (e) => { if (e.key === 'Enter' && own.value.trim()) { e.preventDefault(); answer(own.value.trim()); } });
  const { title, details: all, task, long, sub } = splitQuestion(q);
  // A line `---` splits what he reads from what the agent needs (commands, ids): the latter folds away.
  const cut = all.search(/\n-{3,}\n|\n\*\*How to remove\*\*/);
  const details = (cut < 0 ? all : all.slice(0, cut)).trim();
  const forAgent = cut < 0 ? '' : all.slice(cut).replace(/^\n-{3,}\n/, '').trim();
  const ctx = details && dropShown(shortLinks(h('div', { class: 'md qcontext', html: markdown(details) })), q);
  if (ctx) quoteCopy(ctx);
  // Two groups (three with pictures): on a wide screen the deck lays them side by side (what to read | what to
  // look at | what to pick), so a card fits without scrolling; elsewhere the card reads top to bottom.
  const main = h('div', { class: 'qmain' }, ...[
    h('div', { class: 'qtop' }, projChip(q.project, sub), h('span', {}, ago(q.created_at)),
      q.blocking && h('span', { class: 'wait' }, 'Agent is waiting'), flagButton(q)),
    q.flagged_at && q.flag && h('p', { class: 'qflag-said' }, `👎 ${q.flag}`),
    h('h3', { class: `qtext${long ? ' long' : ''}` }, h('a', { href }, todo ? title.replace(/\s*\?\s*$/, '') : title)),
    task && h('p', { class: 'qtask' }, `📋 ${task}`),
    // A by-hand card reads why → do this → done; any other card shows what to look at first.
    q.steps?.length && ctx && details.length <= STEPS_CONTEXT && ctx,
    !(q.steps?.length && details.length <= STEPS_CONTEXT) && ctx && (details.length > LONG_CONTEXT ? h('details', { class: 'more-info' }, h('summary', {}, 'More details'), ctx) : ctx),
    forAgent && h('details', { class: 'more-info' }, h('summary', {}, 'For the agent'),
      shortLinks(h('div', { class: 'md qcontext', html: markdown(forAgent) }))),
  ].filter(Boolean));
  const side = h('div', { class: 'qside' }, ...[
    stepsBlock(q),
    seeBlock(q),
    opts.length > 0 && h('div', { class: 'opts' }, opts),
    h('div', { class: 'answer-own' }, own, send),
    // No "remember" checkbox (2026-11-22): rules come from his repeated answers, proposed with the evidence.
    q.gate_blocking && !q.blocking && h('p', { class: 'gate-note', title: `Approval gate, not on yet: ${q.blocking_reason}` }, '🛡️ Once the approval gate is on, the agent waits for this one'),
    foot && h('div', { class: 'qfoot' },
      h('button', { class: 'skip', onclick: () => dismissQuestion(q, onChange) }, 'Not needed')),
  ].filter(Boolean));
  el.append(...[main, seePics(q), side].filter(Boolean));
  return el;
}
