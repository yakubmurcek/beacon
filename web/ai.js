'use strict';
// AI jobs (http://beacon/ai): every job that runs a model by itself, on which model and effort, what it costs.
// Data: GET /api/ai (hub/aijobs.py), the last 7 days at API list prices. Refreshes every 2 minutes.

const SHOW = 10;  // jobs shown before "show the smaller ones"
let last = null;
let showAll = false;

const money = (v) => (v >= 100 ? `$${Math.round(v)}` : v >= 10 ? `$${v.toFixed(0)}` : v >= 0.995 ? `$${v.toFixed(2)}` : v > 0 ? `${Math.max(1, Math.round(v * 100))}¢` : '$0');
const plural = (k, one, many) => `${k.toLocaleString('en')} ${k === 1 ? one : many || one + 's'}`;
const WEEKDAY = (d) => new Date(d + 'T12:00:00').toLocaleDateString(window.hubLocale || 'en', { weekday: 'short' });

// One color per model family, the same on every mark.
function modelColor(name) {
  const n = (name || '').toLowerCase();
  if (n.startsWith('opus')) return 'var(--accent)';
  if (n.startsWith('sonnet')) return 'var(--s1)';
  if (n.startsWith('haiku')) return 'var(--s3)';
  if (n.startsWith('fable')) return 'var(--s7)';
  if (n.includes('deepseek')) return 'var(--s4)';
  if (n.includes('minimax')) return 'var(--s5)';
  return 'var(--other)';
}

function hero(d) {
  const t = d.total;
  const sums = d.days.map((_, i) => d.jobs.reduce((s, j) => s + (j.days[i] || 0), 0));
  const max = Math.max(...sums, 0.01);
  const bars = sums.map((v, i) => `<div class="day${i === sums.length - 1 ? ' today' : ''}">
      <b>${v >= max * 0.999 && v > 0 ? money(v) : ''}</b>
      <i style="height:${v > 0 ? Math.max(4, (v / max) * 100) : 2}%"></i>
      <span>${i === sums.length - 1 ? 'today' : WEEKDAY(d.days[i])}</span></div>`).join('');
  const share = t.share_of_all != null ? Math.round(t.share_of_all * 100) : null;
  const runs = d.jobs.reduce((s, j) => s + j.runs, 0);
  return `<section class="hero">
    <div class="hero-main">
      <p class="kicker">AI jobs · last 7 days</p>
      <h1>${money(t.cost)}</h1>
      <p class="title">API value of model runs nobody typed</p>
      ${share != null ? `<div class="share" title="Of all agent usage this week, at API list prices (you pay a flat plan)">
        <div class="share-bar"><i style="width:${Math.max(2, share)}%"></i></div>
        <span><b>${share}%</b> of all your agent usage (${money(t.all_cost)})</span></div>` : ''}
      <p class="sub">${plural(t.jobs, 'job')} · ${plural(runs, 'run')} · ${plural(t.calls, 'model call')}</p>
    </div>
    <div class="days" aria-label="Spend per day">${bars}</div>
  </section>`;
}

function hint(j) {
  const top = j.models[0];
  if (!top || !j.cost) return '';
  const share = top.cost / j.cost;
  if (top.name.toLowerCase().startsWith('opus') && share > 0.6 && j.runs >= 10) {
    return `<span class="chip warn" title="Opus is the priciest model; a job this frequent may do as well on Sonnet">Opus on a frequent job</span>`;
  }
  return '';
}

function modelChips(j) {
  if (!j.models.length) {
    const asks = j.asks.models;
    return asks.length ? asks.map((m) => `<span class="chip model" style="--c:${modelColor(m)}">${esc(m)} <em>set</em></span>`).join('') : '';
  }
  const total = j.models.reduce((s, m) => s + (j.cost ? m.cost : m.calls), 0) || 1;
  return j.models.slice(0, 3).map((m) => {
    if (m.nolog) return `<span class="chip model" style="--c:${modelColor(m.name)}" title="Read from its command line: these runs keep no log">${esc(m.name)} <em>no log</em></span>`;
    const pct = Math.round(((j.cost ? m.cost : m.calls) / total) * 100);
    return `<span class="chip model" style="--c:${modelColor(m.name)}">${esc(m.name)}${j.models.length > 1 ? ` <em>${pct}%</em>` : ''}</span>`;
  }).join('');
}

function effortChip(j) {
  const e = j.efforts[0]?.name || (j.asks.efforts[0] ? j.asks.efforts[0] : null);
  if (!e) return '';
  const used = j.efforts.length && !j.efforts[0].nolog;
  return `<span class="chip effort" title="${used ? 'Reasoning effort its runs used' : 'Effort its command asks for'}">${esc(e)} effort${used ? '' : ' <em>set</em>'}</span>`;
}

function spark(days) {
  const max = Math.max(...days, 0) || 1, w = 84, hgt = 26, bw = w / days.length;
  return `<svg class="spark" viewBox="0 0 ${w} ${hgt}" preserveAspectRatio="none" aria-hidden="true">${days.map((v, i) => {
    const bh = v > 0 ? Math.max(2, (v / max) * hgt) : 1;
    return `<rect x="${i * bw + 1}" y="${hgt - bh}" width="${bw - 2}" height="${bh}" class="${v > 0 ? '' : 'zero'}"/>`;
  }).join('')}</svg>`;
}

function when(j) {
  const bits = [];
  if (j.kind) bits.push(j.kind);
  if (j.schedule) bits.push(j.schedule);
  if (j.last) bits.push(`last ran ${ago(j.last)}`);
  if (j.next_run && j.next_run > Date.now() / 1000) bits.push(`next ${new Date(j.next_run * 1000).toLocaleTimeString(window.hubLocale || 'en-GB', { hour: '2-digit', minute: '2-digit' })}`);
  return bits.map(esc).join(' · ');
}

function row(j, top) {
  const w = top > 0 ? Math.max(3, (j.cost / top) * 100) : 0;
  const color = modelColor(j.models[0]?.name || j.asks.models[0]);
  const per = j.per_run != null ? `${money(j.per_run)} a run` : null;
  const runs = `${plural(j.runs, 'run')}${j.runs && j.runs_logged < j.runs ? ` (${j.runs - j.runs_logged} with no log)` : ''}`;
  return `<article class="job${j.linked ? '' : ' loose'}">
    <div class="job-head">
      <h3 title="${esc(j.what || '')}">${esc(j.name)}</h3>
      <span class="box">${esc(j.machine)}</span>
    </div>
    ${j.what ? `<p class="what">${esc(j.what)}</p>` : ''}
    <div class="bar"><i style="width:${w}%;--c:${color}"></i><b>${!j.measured ? (j.runs ? 'cost not logged' : '') : !j.cost && !j.priced ? 'no price yet' : (j.floor ? '≥ ' : '') + money(j.cost)}</b><span>${j.cost ? `${Math.round(j.share * 100)}% of job spend` : ''}</span></div>
    <div class="chips">${modelChips(j)}${effortChip(j)}<span class="chip plain">${esc(runs)}</span>${per ? `<span class="chip plain">${per}</span>` : ''}${hint(j)}</div>
    <div class="job-foot"><span>${when(j)}</span>${spark(j.days)}</div>
  </article>`;
}

function smallList(title, note, items, render) {
  if (!items.length) return '';
  return `<section class="panel">
    <div class="row"><h2>${title}</h2><span class="note">${note}</span></div>
    <div class="mini">${items.map(render).join('')}</div>
  </section>`;
}

function mini(j, label) {
  const tools = j.providers.length ? j.providers : j.tools;
  const models = j.asks.models.length ? j.asks.models : [];
  return `<div class="mini-job" title="${esc(j.what || '')}"><b>${esc(j.name)}</b><span class="box">${esc(j.machine)}</span>
    <span class="chips">${tools.map((t) => `<span class="chip plain">${esc(t)}</span>`).join('')}${models.map((m) => `<span class="chip model" style="--c:${modelColor(m)}">${esc(m)}</span>`).join('')}${j.asks.efforts[0] ? `<span class="chip effort">${esc(j.asks.efforts[0])} effort</span>` : ''}${label ? `<span class="chip muted">${label}</span>` : ''}</span></div>`;
}

function render(d) {
  const ran = d.jobs.filter((j) => j.measured || j.runs);
  const quiet = d.jobs.filter((j) => !j.measured && !j.runs && !j.unmeasurable);
  const blind = d.jobs.filter((j) => !j.measured && j.unmeasurable);
  const shown = showAll ? ran : ran.slice(0, SHOW);
  const top = Math.max(...ran.map((j) => j.cost), 0);
  const more = ran.length - shown.length;
  const rest = ran.slice(SHOW).reduce((s, j) => s + j.cost, 0);
  $('view').innerHTML = hero(d)
    + `<section class="panel">
        <div class="row"><h2>Where it goes</h2><span class="note">priciest first</span></div>
        <div class="jobs">${shown.map((j) => row(j, top)).join('') || '<p class="note">No model runs without a person this week.</p>'}</div>
        ${more > 0 ? `<button class="more" id="more">Show ${plural(more, 'smaller job')} (${money(rest)} together)</button>` : ''}
        ${showAll && ran.length > SHOW ? '<button class="more" id="more">Show fewer</button>' : ''}
      </section>`
    + smallList('Not measured', 'they call a model directly, with no log to price', blind, (j) => mini(j, 'cost not measured'))
    + smallList('Set up, no runs this week', 'their scripts call a model', quiet, (j) => mini(j, j.state === 'failed' ? 'failing' : ''));
  const btn = $('more');
  if (btn) btn.onclick = () => { showAll = !showAll; render(last); };
}

// The JSON last drawn: a refresh that brings the same numbers leaves the page alone (no blink, scroll kept).
let painted = '';
function show(d) {
  const s = JSON.stringify(d);
  if (s === painted) return false;
  painted = s;
  last = d;
  render(d);
  return true;
}

async function load() {
  try {
    const d = await api('/api/ai');
    showError(null);
    if (show(d)) snap.set('ai', d);
  } catch (e) {
    showError(e);
  }
}

// paint the last snapshot at once; an old one that no longer fits the page just waits for the fresh data
try { const kept = snap.get('ai'); if (kept) show(kept); } catch { painted = ''; }
load();
setInterval(() => { if (!document.hidden) load(); }, 120000);
