'use strict';
// AI Rank (http://beacon/rank): the badge, how it moved in 30 days, the live numbers behind it, what went out.
// Data: GET /api/rank (hub/rank.py), fed hourly by the ai-rank collector. Refreshes every 5 minutes.
// TIERS, color() and emblem() come from rank-badge.js.

function hero(d) {
  const n = d.now, up = n.change.score;
  const move = up === 0 ? 'Same as 30 days ago' : `${up > 0 ? '▲ Up' : '▼ Down'} from ${esc(n.change.from)} 30 days ago`;
  const warn = n.warning ? `<p class="warn">⚠️ Drops a tier on <b>${dayLabel(n.warning.drop)}</b> if nothing new comes in (${n.warning.last ? `last ${esc(n.warning.what)} on ${dayLabel(n.warning.last)}` : `no ${esc(n.warning.what)} in 30 days`}).</p>` : '';
  return `<section class="hero" style="--c:${color(n.tier)}">
    ${emblem(n.tier, 132)}
    <div class="hero-main">
      <p class="kicker">AI Rank</p>
      <h1>${esc(n.label)}</h1>
      <p class="title">${esc(n.title)}</p>
      <div class="lp" title="LP: how far toward the next tier in the last 30 days, 0-100">
        <div class="lp-bar"><i style="width:${n.lp}%"></i></div><span><b>${n.lp}</b> LP</span>
      </div>
      <p class="move ${up > 0 ? 'up' : up < 0 ? 'down' : ''}">${move}</p>
    </div>
    <div class="next"><span>Next</span><b>${esc(n.next)}</b>${warn}</div>
  </section>`;
}

function chart(hist, width) {
  const lo = Math.min(...hist.map((h) => TI[h.tier])), hi = Math.max(...hist.map((h) => TI[h.tier]));
  const y0 = lo * 100, y1 = (hi + 1) * 100;
  const W = Math.max(300, Math.round(width)), H = W < 520 ? 210 : 260, L = 2, R = 8, T = 10, B = 24;  // drawn at real pixels: text stays readable
  const x = (i) => L + (i / (hist.length - 1)) * (W - L - R);
  const y = (s) => T + (1 - (s - y0) / (y1 - y0)) * (H - T - B);
  const bands = [];
  for (let t = lo; t <= hi; t++) {
    bands.push(`<rect x="${L}" y="${y((t + 1) * 100)}" width="${W - L - R}" height="${y(t * 100) - y((t + 1) * 100)}" fill="${TIERS[t][2]}" opacity="${t % 2 ? 0.16 : 0.1}"/>`);
    bands.push(`<text x="${L + 6}" y="${y((t + 1) * 100) + 15}" class="band-label" fill="${TIERS[t][2]}">${TIERS[t][1]}</text>`);
  }
  let path = '';
  hist.forEach((h, i) => { path += `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(h.score).toFixed(1)}`; });
  const dots = hist.map((h, i) => `<circle cx="${x(i)}" cy="${y(h.score)}" r="${i === hist.length - 1 ? 6 : 3.2}" fill="${color(h.tier)}" stroke="var(--text)" stroke-width="1.5"/>`).join('');
  const ticks = [0, 7, 14, 21, 30].filter((i) => i < hist.length).map((i) => `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === hist.length - 1 ? 'end' : 'middle'}" class="tick">${i === hist.length - 1 ? 'today' : dayLabel(hist[i].day)}</text>`).join('');
  const hits = hist.map((h, i) => `<rect class="hit" data-i="${i}" x="${x(i) - (W - L - R) / hist.length / 2}" y="${T}" width="${(W - L - R) / hist.length}" height="${H - T - B}" fill="transparent"/>`).join('');
  return `<section class="panel chart-panel">
    <div class="row"><h2>Last 30 days</h2>
      <span class="legend"><span><i class="ln"></i>your rank each day (tier + LP)</span><span><i class="bd"></i>tier bands</span></span></div>
    <div class="chart-wrap"><svg id="rank-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Rank over the last 30 days">
      ${bands.join('')}<path d="${path}" fill="none" stroke="var(--text)" stroke-width="2.5" stroke-linejoin="round"/>
      ${dots}${ticks}${hits}</svg><div class="tip" id="tip" hidden></div></div>
  </section>`;
}

function spark(s) {
  const max = Math.max(...s.spark, 0) || 1, n = s.spark.length, w = 120, hgt = 34, bw = w / n;
  return `<svg class="spark" viewBox="0 0 ${w} ${hgt}" preserveAspectRatio="none" aria-hidden="true">${s.spark.map((v, i) => {
    const bh = v > 0 ? Math.max(2, (v / max) * hgt) : 1;
    return `<rect x="${i * bw + 0.5}" y="${hgt - bh}" width="${bw - 1}" height="${bh}" class="${v > 0 ? (i >= n - 7 ? 'wk' : '') : 'zero'}"/>`;
  }).join('')}</svg>`;
}

function signals(list) {
  return `<section class="signals">${list.map((s) => {
    const delta = s.last7 - s.prev7;
    const dir = delta > 0 ? 'up' : delta < 0 ? 'down' : '';
    // Roblox and Apple report a day or two late: their week is the last 7 days they reported
    const wk = s.behind ? `in the 7 days to ${dayLabel(pragueToday(-s.behind))}` : 'this week';
    const week = s.unit === 'avg' ? `${fmtN(s.last7 / 7)}/day ${wk}, ${fmtN(s.prev7 / 7)} the week before`
      : `${fmtV(s, s.last7)} ${wk}, ${fmtV(s, s.prev7)} the week before`;
    return `<article class="sig ${s.ok ? '' : 'off'}" title="${esc(s.how)}">
      <div class="sig-top"><h3>${esc(s.label)}</h3>${s.unlocks ? `<span class="unlock">${esc(s.unlocks)}</span>` : ''}</div>
      <div class="big">${s.ok ? fmtV(s, s.value) : '–'}</div>
      <p class="sub">${s.unit === 'avg' ? 'a day, last 30 days' : 'last 30 days'}</p>
      ${spark(s)}
      <p class="wk ${dir}">${s.ok ? `${dir === 'up' ? '▲' : dir === 'down' ? '▼' : '='} ${week}` : 'Not read: its source is down'}</p>
    </article>`;
  }).join('')}</section>`;
}

function ladder(cur) {
  const ci = TI[cur];
  return `<section class="panel">
    <div class="row"><h2>The ladder</h2><span class="legend"><span><i class="sq got"></i>reached</span><span><i class="sq"></i>ahead</span></span></div>
    <p class="note">Every gate counts the last 30 days. Sessions, tasks and PRs count only below Silver. Money counts only once it is yours (your half of the Robux, and only after 30,000 Robux can be cashed out) and after what went on ads.</p>
    <ol class="ladder">${TIERS.map((t, i) => `<li class="${i < ci ? 'got' : i === ci ? 'here' : ''}" style="--c:${t[2]}">
      <b>${t[1].replace('Grandmaster', 'Grand\u00ADmaster')}</b><span>${t[3]}</span></li>`).join('')}</ol>
  </section>`;
}

// Vercel project names that pname() reads badly
const SITE_NAMES = window.BEACON.site_names;  // settings `site_names`

function shipped(rel, rb) {
  const name = (r) => (r.kind === 'web' ? `${SITE_NAMES[r.what] || pname(r.what)} live on the web` : r.what);
  const icon = { web: '🌐', appstore: '📱', roblox: '🎮' };
  const row = (r) => `<li><span class="d">${dayLabel(r.day)}</span><span class="i">${icon[r.kind] || '•'}</span><span>${esc(name(r))}</span></li>`;
  const now = rb ? `<p class="note">🎮 ${esc(rb.name || window.BEACON.game)} right now: <b>${fmtN(rb.playing || 0)}</b> playing, ${fmtN(rb.visits || 0)} visits all time.</p>` : '';
  return `<section class="panel">
    <div class="row"><h2>What went out</h2><span class="legend"><span>🌐 web</span><span>📱 App Store</span><span>🎮 Roblox</span></span></div>
    ${rel.length ? `<ul class="ship">${rel.slice(0, 6).map(row).join('')}</ul>
      ${rel.length > 6 ? `<details><summary>All ${rel.length} in 30 days</summary><ul class="ship">${rel.slice(6).map(row).join('')}</ul></details>` : ''}`
      : '<p class="note">Nothing went out in the last 30 days.</p>'}
    ${now}
  </section>`;
}

function sources(d) {
  const r = d.rates;
  const rate = r && typeof r.USD === 'number' && typeof r.devex_usd === 'number'
    ? `Robux at the DevEx rate ($${esc(r.devex_usd)} each, paid out from 30,000), dollars at ${String(r.from).startsWith('cnb') ? "the Czech National Bank's" : "the day's"} rate (${r.USD.toFixed(2)} Kč).` : '';
  return `<p class="sources">${d.sources.map((s) => `<span class="${s.ok ? 'ok' : 'bad'}" title="${esc(s.ok ? '' : s.why || '')}">${s.ok ? '●' : '○'} ${esc(s.name)}${s.ok ? '' : ' not read'}</span>`).join('')}
    <span>Updated ${d.at ? ago(d.at) : 'never'}</span></p><p class="sources">${rate} Tasks and sessions are Beacon's own.</p>`;
}

function wireTip(hist) {
  const svg = $('rank-chart'), tip = $('tip');
  if (!svg) return;
  const show = (e) => {
    const t = e.target.closest('.hit');
    if (!t) { tip.hidden = true; return; }
    const h = hist[+t.dataset.i];
    tip.innerHTML = `<b>${dayLabel(h.day)}</b> ${esc(h.label)} · ${h.lp} LP`;
    tip.hidden = false;
    const box = svg.getBoundingClientRect(), r = t.getBoundingClientRect();
    tip.style.left = `${Math.min(Math.max(r.left - box.left + r.width / 2, 60), box.width - 60)}px`;
  };
  svg.addEventListener('pointermove', show);
  svg.addEventListener('pointerdown', show);
  svg.addEventListener('pointerleave', () => { tip.hidden = true; });
}

let last = null;
function render(d) {
  $('view').innerHTML = hero(d) + chart(d.history, 600) + signals(d.signals) + shipped(d.releases, d.roblox_now) + ladder(d.now.tier) + sources(d);
  // drawn again at the panel's real width once laid out, so the labels keep their pixel size
  const panel = document.querySelector('.chart-panel');
  panel.outerHTML = chart(d.history, panel.querySelector('.chart-wrap').clientWidth);
  wireTip(d.history);
}

// The JSON last drawn: a refresh that brings the same numbers leaves the page alone (no blink, open lists stay open).
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
    const d = await api('/api/rank');
    showError(null);
    if (show(d)) snap.set('rank', d);
  } catch (e) {
    showError(e);
  }
}

// paint the last snapshot at once; an old one that no longer fits the page just waits for the fresh data
try { const kept = snap.get('rank'); if (kept) show(kept); } catch { painted = ''; }
load();
let resizeTimer;
addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => last && render(last), 150); });
setInterval(() => { if (!document.hidden) load(); }, 300000);
