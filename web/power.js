'use strict';
// Power: the PC's CPU + GPU watts (wall power estimated) and the Shelly plugs, priced by the hour.
// The data is hal-power's on the home server (the plugs are on the home LAN); the hub passes it through as /api/power.
const $ = (id) => document.getElementById(id);
let hours = 24;
let last = null;
const SNAP = 'hub.snap.power';  // same key scheme as shared.js's snap (this page does not load it)
let painted = '';  // the JSON last drawn: the same data again leaves the charts alone
const fmt = (n, d = 1) => n.toLocaleString('cs-CZ', { minimumFractionDigits: d, maximumFractionDigits: d });
const kc = (n) => fmt(n, n < 10 ? 2 : 0) + ' Kč';
const el = (tag, attrs, text) => {
  const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
};
const pragueDay = (t) => new Date(t).toLocaleDateString('sv-SE', { timeZone: 'Europe/Prague' });

function lineChart(svg, series, bucketMin) {
  svg.replaceChildren();
  const W = svg.clientWidth || 900, H = W < 520 ? 200 : 260, L = 48, B = 22, T = 10;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.style.height = H + 'px';
  if (!series.length) { svg.append(el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, 'No data yet')); return; }
  const ts = series.map((p) => +new Date(p.t));
  const t1 = Date.now(), t0 = t1 - hours * 3600e3;
  const ymax = Math.max(50, ...series.map((p) => p.wall)) * 1.1;
  const x = (t) => L + (t - t0) / (t1 - t0) * (W - L - 4), y = (v) => T + (1 - v / ymax) * (H - T - B);
  for (let v = 0; v <= ymax; v += ymax > 300 ? 100 : ymax > 150 ? 50 : 25) {
    svg.append(el('line', { x1: L, x2: W, y1: y(v), y2: y(v), stroke: 'var(--line)' }));
    svg.append(el('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }, v + ' W'));
  }
  const ticks = W < 520 ? 3 : hours <= 168 && hours > 24 ? 7 : 6;
  for (let i = 0; i <= ticks; i++) {
    const t = t0 + (t1 - t0) * i / ticks, d = new Date(t);
    const lab = hours <= 24 ? d.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' }) : d.toLocaleDateString('cs-CZ', { day: 'numeric', month: 'numeric' });
    svg.append(el('text', { x: x(t), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === ticks ? 'end' : 'middle' }, lab));
  }
  const gap = bucketMin * 60e3 * 2.5;  // break the line where the box was off
  for (const [key, color, width] of [['gpu', 'var(--gpu)', 1.5], ['cpu', 'var(--cpu)', 1.5], ['wall', 'var(--wall)', 2.2]]) {
    let d = '';
    series.forEach((p, i) => { d += (i && ts[i] - ts[i - 1] < gap ? 'L' : 'M') + x(ts[i]).toFixed(1) + ' ' + y(p[key]).toFixed(1); });
    svg.append(el('path', { d, fill: 'none', stroke: color, 'stroke-width': width, 'stroke-linejoin': 'round', 'vector-effect': 'non-scaling-stroke' }));
  }
}

function dayBars(svg, daily) {
  svg.replaceChildren();
  const W = svg.clientWidth || 900, H = 190, B = 36, T = 20;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.style.height = H + 'px';
  if (!daily.length) { svg.append(el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, 'No data yet')); return; }
  daily = daily.slice(-Math.max(7, Math.floor(W / 48)));  // bars at least ~48 px wide
  const n = Math.max(daily.length, 7), bw = W / n, max = Math.max(...daily.map((d) => d.czk)) * 1.15 || 1;
  daily.forEach((d, i) => {
    const h = d.czk / max * (H - T - B), cx = i * bw + bw / 2;
    const r = el('rect', { x: i * bw + bw * .16, y: H - B - h, width: bw * .68, height: h, rx: 6, fill: 'var(--wall)', opacity: d.minutes < 1380 ? .45 : 1 });
    r.append(el('title', {}, `${fmt(d.kwh, 2)} kWh · ${kc(d.czk)}${d.minutes < 1380 ? ` · ${fmt(d.minutes / 60, 1)} h logged` : ''}`));
    svg.append(r);
    svg.append(el('text', { x: cx, y: H - B - h - 5, 'text-anchor': 'middle', class: 'val' }, fmt(d.czk, 0) + ' Kč'));
    svg.append(el('text', { x: cx, y: H - 20, 'text-anchor': 'middle' }, new Date(d.day + 'T12:00').toLocaleDateString('cs-CZ', { weekday: 'short' })));
    svg.append(el('text', { x: cx, y: H - 5, 'text-anchor': 'middle' }, new Date(d.day + 'T12:00').toLocaleDateString('cs-CZ', { day: 'numeric', month: 'numeric' })));
  });
}

function ranges(h) {  // "0–5, 15–18" from [0,1,2,3,4,15,16,17]
  const out = [];
  for (const x of h) { const r = out[out.length - 1]; if (r && r[1] === x) r[1] = x + 1; else out.push([x, x + 1]); }
  return out.map(([a, b]) => `${a}–${b}`).join(', ');
}

function td(text, cls) { const c = document.createElement('td'); c.textContent = text; if (cls) c.className = cls; return c; }

function plugTable(j) {
  const today = pragueDay(Date.now()), ym = today.slice(0, 7), wk = pragueDay(Date.now() - 6 * 864e5);
  const sum = (ds, f) => ds.reduce((a, d) => a + f(d), 0);
  const tot = { now: 0, d: 0, w: 0, m: 0, mk: 0, mn: 0 };
  const rows = j.plugs.map((p) => {
    const age = (Date.now() - new Date(p.t)) / 60e3;
    const d = sum(p.daily.filter((x) => x.day === today), (x) => x.czk);
    const w = sum(p.daily.filter((x) => x.day >= wk), (x) => x.czk);
    const mo = p.daily.filter((x) => x.day.startsWith(ym));
    const m = sum(mo, (x) => x.czk), mk = sum(mo, (x) => x.vt_kwh + x.nt_kwh), mn = sum(mo, (x) => x.nt_kwh);
    Object.assign(tot, { now: tot.now + (age < 15 ? p.now_w : 0), d: tot.d + d, w: tot.w + w, m: tot.m + m, mk: tot.mk + mk, mn: tot.mn + mn });
    const now = age < 15 ? fmt(p.now_w, 0) + ' W' : `off · ${age < 120 ? Math.round(age) + ' min' : Math.round(age / 60) + ' h'} ago`;
    return { name: p.name, cells: [now, kc(d), kc(w), kc(m), fmt(mk, 2)], mn, mk };
  });
  if (rows.length > 1) rows.push({ name: 'All plugs', total: true, cells: [fmt(tot.now, 0) + ' W', kc(tot.d), kc(tot.w), kc(tot.m), fmt(tot.mk, 2)], mn: tot.mn, mk: tot.mk });
  if (!rows.length) {
    const tr = document.createElement('tr'); const c = td('No plugs found yet. A Shelly plug on the home WiFi shows up within the hour.', 'empty'); c.colSpan = 7; tr.append(c);
    return $('plugs').replaceChildren(tr);
  }
  $('plugs').replaceChildren(...rows.map((r) => {
    const tr = document.createElement('tr'); if (r.total) tr.className = 'total';
    tr.append(td(r.name), ...r.cells.map((v, k) => td(v, k === 4 ? 'kwh' : '')));
    const c = document.createElement('td'), share = r.mk ? r.mn / r.mk : 0;
    const bar = document.createElement('span'); bar.className = 'nt'; bar.style.width = Math.max(2, Math.round(share * 30)) + 'px';
    c.append(bar, Math.round(share * 100) + ' %'); tr.append(c);
    return tr;
  }));
}

function render(j) {
  last = j;
  const t = j.tariff;
  $('tariff').textContent = `Prices: day ${fmt(t.vt, 2)} Kč/kWh, night ${fmt(t.nt, 2)} Kč/kWh (${ranges(t.nt_hours)} h), plus about ${fmt(t.fixed_month, 0)} Kč a month in fixed fees whatever you use. Plugs are read every 2 min through the PC; while it sleeps, the gap is spread over the hours. Name a plug in its own web page (Settings → Device name).`;
  if (j.now) {
    const age = (Date.now() - new Date(j.now.t)) / 60e3;
    $('now').textContent = fmt(j.now.wall, 0) + ' W';
    $('nowparts').textContent = age > 12 ? `asleep or off · last ${Math.round(age)} min ago` : `CPU ${fmt(j.now.cpu, 0)} W · GPU ${fmt(j.now.gpu, 0)} W`;
  }
  const lab = hours === 24 ? 'Last 24 h' : hours === 168 ? 'Last 7 days' : 'Last 30 days';
  $('winlabel').textContent = lab;
  $('winkwh').textContent = kc(j.window.czk);
  $('winczk').textContent = `${fmt(j.window.kwh, 2)} kWh · ${fmt(j.window.minutes / 60, 1)} h on`;
  const ym = pragueDay(Date.now()).slice(0, 7);  // days are Prague days
  const month = j.daily.filter((d) => d.day.startsWith(ym));
  const mk = month.reduce((a, d) => a + d.kwh, 0), mc = month.reduce((a, d) => a + d.czk, 0), mm = month.reduce((a, d) => a + d.minutes, 0);
  $('month').textContent = fmt(mc, 0) + ' Kč'; $('monthkwh').textContent = fmt(mk, 1) + ' kWh, the PC';
  if (mm > 60) {
    const days = new Date(+ym.slice(0, 4), +ym.slice(5), 0).getDate();
    $('proj').textContent = fmt(mc / mm * 1440 * days, 0) + ' Kč';
    $('projs').textContent = `${fmt(mk / mm * 1440 * days, 0)} kWh if always on`;
  }
  lineChart($('chart'), j.series, j.window.bucket_min);
  dayBars($('days'), j.daily);
  plugTable(j);
  $('appstitle').textContent = 'What used it · ' + lab.toLowerCase();
  const top = Math.max(...j.apps.map((a) => a.kwh), 1e-9);
  $('apps').replaceChildren(...j.apps.slice(0, 12).map((a) => {
    const tr = document.createElement('tr'); if (a.app.startsWith('PC idle')) tr.className = 'idle';
    const h = a.active_min / 60;
    const name = td(a.app);
    const bar = document.createElement('span'); bar.className = 'bar'; bar.style.width = (a.kwh / top * 100) + '%'; name.append(bar);
    tr.append(name, td(kc(a.czk)), td(fmt(a.kwh, 2), 'kwh'), td(fmt(a.avg_w, 0) + ' W'), td(h < 1 ? Math.round(a.active_min) + ' min' : fmt(h, 1) + ' h'));
    return tr;
  }));
  $('model').textContent = `The PC's whole watts = (CPU + GPU + ${j.model.base_w} W for the rest) ÷ ${j.model.psu_eff} power-supply efficiency; CPU and GPU are measured. Apps share the power above idle by their CPU time and GPU use. Monitor not included. Updates every 5 min.`;
}

async function load() {
  try {
    const r = await fetch('/api/power?hours=' + hours, { cache: 'no-store' });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
    const j = await r.json();
    $('error').hidden = true;
    const s = JSON.stringify(j);
    if (s === painted) return;  // nothing new: no redraw, no blink
    painted = s;
    render(j);
    if (hours === 24) try { localStorage.setItem(SNAP, s); localStorage.removeItem('power.snap'); } catch { /* full or blocked */ }
  } catch (e) {
    $('error').textContent = `Power data is not answering (${e.message}). Showing the last numbers.`;
    $('error').hidden = false;
  }
}

$('seg').onclick = (e) => {
  const b = e.target.closest('button'); if (!b) return;
  hours = +b.dataset.h;
  for (const x of $('seg').children) x.classList.toggle('on', x === b);
  load();
};
let rt;
addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { if (last) { lineChart($('chart'), last.series, last.window.bucket_min); dayBars($('days'), last.daily); } }, 150); });
// paint the last 24 h snapshot at once (the old key too, once), then the fresh data only if it differs
try {
  const s = localStorage.getItem(SNAP) || localStorage.getItem('power.snap');
  if (s) { render(JSON.parse(s)); painted = s; }
} catch { painted = ''; /* none yet, or one that no longer fits */ }
load();
setInterval(() => { if (!document.hidden) load(); }, 60e3);
