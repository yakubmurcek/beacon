'use strict';
// Outreach: every lead and business we researched or scraped (Leads), and every research report (Research).
// Data: /api/outreach, read by the hub from ai-brain (hub/outreach.py). The last answer paints at once from
// localStorage, then the fresh one replaces it.
let painted = '';  // the JSON last drawn: a refresh with the same data keeps the list, scroll and search as they are
const STEP = { leads: 25, research: 30 };
const STATUS = [  // order, colour and plain word for each lead status (the legend under the bar says the same)
  ['client', 'var(--good)', 'Client'],
  ['meeting', '#1c5cab', 'Meeting'],
  ['replied', 'var(--s1)', 'Replied'],
  ['sent', 'var(--s5)', 'Sent'],
  ['contacted', 'var(--s4)', 'Contacted'],
  ['drafted', 'var(--accent)', 'Draft, not sent'],
  ['not contacted', 'var(--dim)', 'Not contacted'],
  ['rejected', 'var(--bad)', 'Rejected'],
  ['dropped', '#8f8676', 'Dropped'],
];
// the band across the top of a card: where this lead stands, said as what it means for him
const BAND = { client: ['star', 'Client'], meeting: ['user', 'Meeting set'], replied: ['mail', 'They replied'],
  sent: ['mail', 'Sent, waiting for a reply'], contacted: ['phone', 'Contacted'], drafted: ['send', 'Draft ready to send'],
  'not contacted': ['user', 'Not contacted yet'], rejected: ['x', 'Rejected'], dropped: ['x', 'Dropped'] };
function bandOf(r) {  // [icon, word, colour]: the band and the State filter say the same
  const closed = r.ad === 'closed' && ['drafted', 'not contacted'].includes(r.status);
  if (closed) return ['x', r.status === 'drafted' ? "Ad closed: don't send" : 'Ad closed: skip it', 'var(--bad)'];
  const [icn, word] = BAND[r.status] || ['target', SWORD[r.status] || r.status];
  return [icn, word, SCOLOR[r.status] || 'var(--dim)'];
}
function band(r) {
  const [icn, word, col] = bandOf(r);
  return h('div', { class: 'band', style: `--sc:${col}` }, ic(icn), word);
}
const SCOLOR = Object.fromEntries(STATUS.map(([k, c]) => [k, c]));
const SWORD = Object.fromEntries(STATUS.map(([k, , w]) => [k, w]));
const CHANCE = { high: ['High', 'var(--good)'], mid: ['Medium', 'var(--accent)'], low: ['Low', 'var(--dim)'] };
const ICONS = {
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"/>',
  briefcase: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M3 13h18"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/>',
  building: '<rect x="4" y="3" width="16" height="18" rx="1.5"/><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1"/><path d="M10 21v-3h4v3"/>',
  link: '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
  send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M8 13h8M8 17h5"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  megaphone: '<path d="M3 11v2a1 1 0 0 0 1 1h3l6 4V6L7 10H4a1 1 0 0 0-1 1z"/><path d="M17 9a4 4 0 0 1 0 6"/>',
  door: '<path d="M4 21h16"/><path d="M6 21V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17"/><circle cx="14" cy="12" r="1"/>',
  pin: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4"/><circle cx="12" cy="3" r="1"/><path d="M9 13h.01M15 13h.01"/><path d="M9.5 17h5"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.7 1.8L21.5 17.5l-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z"/>',
  laptop: '<rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19h20"/>',
  chart: '<path d="M4 20V4"/><path d="M4 20h16"/><path d="m7 15 4-4 3 3 5-6"/>',
  home: '<path d="m3 11 9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-5h4v5"/>',
  map: '<path d="m3 6 6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14"/>',
  shapes: '<circle cx="7" cy="7" r="3.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><path d="m7 13.5 4 7H3z"/><circle cx="17" cy="17" r="3.5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.9-3.9L3 9"/><path d="M3 4v5h5"/><path d="M4 13a8 8 0 0 0 14.9 3.9L21 15"/><path d="M21 20v-5h-5"/>',
  sort: '<path d="M7 4v16M3 16l4 4 4-4"/><path d="M17 20V4M13 8l4-4 4 4"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};
const ic = (name, cls) => h('span', { class: 'ic' + (cls ? ' ' + cls : ''), 'aria-hidden': 'true',
  html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ''}</svg>` });
const SECTION_IC = [[/mail/i, 'mail'], [/application/i, 'file'], [/found/i, 'target'], [/role/i, 'briefcase'], [/contact/i, 'user'], [/source/i, 'link'],
  [/website/i, 'globe'], [/review/i, 'star'], [/ads/i, 'megaphone'], [/walk/i, 'door'], [/./, 'building']];
const LABEL_IC = [[/^(person|decision maker|contact)/i, 'user'], [/mail/i, 'mail'], [/phone|tel/i, 'phone'], [/found on|source/i, 'link'],
  [/web|site|domain/i, 'globe'], [/career|role|hiring/i, 'briefcase'], [/where|address|seat|town|branch/i, 'pin'],
  [/rating|review|google|maps/i, 'star'], [/ads?\b|meta/i, 'megaphone'], [/what|kind|legal|name|form|founded|size|employ|revenue|profit|ico/i, 'building'],
  [/date|cv|status|draft/i, 'file'], [/note/i, 'file']];
const labelIc = (label) => { const m = LABEL_IC.find(([re]) => re.test(label || '')); return m ? ic(m[1]) : null; };
const domainOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; } };
const initials = (name) => String(name || '?').replace(/[^\p{L}\p{N} ]/gu, ' ').trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
function avatar(name, url, big) {  // the firm's own logo when its site has one, else its initials in the group's colour
  const box = h('span', { class: 'av' + (big ? ' big' : '') }, h('b', null, initials(name)));
  const d = domainOf(url);
  if (d) {
    const img = h('img', { src: '/outreach/logo?d=' + encodeURIComponent(d), alt: '', loading: 'lazy' });
    img.onload = () => { if (img.naturalWidth > 8) box.classList.add('has-logo'); };
    img.onerror = () => img.remove();
    box.append(img);
  }
  return box;
}
const meter = (level) => {  // chance as three bars: high fills three, medium two, low one
  const n = { high: 3, mid: 2, low: 1 }[level] || 0;
  return h('span', { class: 'meter', style: `--cc:${(CHANCE[level] || [])[1] || 'var(--dim)'}` }, [1, 2, 3].map((i) => h('i', { class: i <= n ? 'on' : '' })));
};
// how much AI a role asks for, read from its ad: three bars like the chance, in their own colour
const AI = { native: [3, 'AI-native'], daily: [2, 'AI daily'], mentioned: [1, 'AI mentioned'], none: [0, 'No AI'] };
const aiChip = (level) => {
  const [n, word] = AI[level] || [0, level];
  return h('span', { class: 'aichip lv' + n }, ic('spark'),
    h('span', { class: 'meter', style: '--cc:var(--cyan)' }, [1, 2, 3].map((i) => h('i', { class: i <= n ? 'on' : '' }))), word);
};
// a picture for each filter value and the card's own lines: a pin for a place, a laptop for IT, a graph for business
const fieldIc = (v) => /\bIT\b|software|dev/i.test(v || '') ? 'laptop' : /econom|business|financ|sales/i.test(v || '') ? 'chart'
  : /engineer|tech|research|science/i.test(v || '') ? 'gear' : 'shapes';
const placeIc = (v) => /remote|home/i.test(v || '') ? 'home' : /elsewhere|abroad|other/i.test(v || '') ? 'map' : 'pin';
function valueGlyph(k, v) {
  if (k === 'State') {
    const hit = Object.entries(BAND).find(([, x]) => x[1] === v);
    const closed = /closed/i.test(v);
    return h('span', { class: 'stglyph', style: `--sc:${closed ? 'var(--bad)' : SCOLOR[(hit || [])[0]] || 'var(--dim)'}` }, ic(closed ? 'x' : hit ? hit[1][0] : 'target'));
  }
  if (k === 'Chance') { const lv = { High: 'high', Medium: 'mid', Low: 'low' }[v.split(' ')[0]]; return lv ? meter(lv) : null; }
  if (k === 'AI') {
    const lv = Object.keys(AI).find((x) => AI[x][1] === v);
    return lv ? h('span', { class: 'aiglyph' }, ic('spark'), h('span', { class: 'meter', style: '--cc:var(--cyan)' }, [1, 2, 3].map((i) => h('i', { class: i <= AI[lv][0] ? 'on' : '' })))) : ic('spark');
  }
  if (k === 'Field') return ic(fieldIc(v));
  if (k === 'Where' || k === 'Town') return ic(placeIc(v));
  if (k === 'Posted' || k === 'Age') return ic('clock', /today|week/i.test(v) ? 'fresh' : '');
  if (k === 'Open role') return ic(/no /i.test(v) ? 'x' : 'briefcase');
  return ic(FACET_IC[k] || 'target');
}
const chanceChip = (level) => h('span', { class: 'chance' }, meter(level), (CHANCE[level] || [level])[0] + ' chance');

const KIND = { research: 'Research', plan: 'Plan', draft: 'Draft', notes: 'Notes', other: 'Page' };
const PCOL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#4a3aa7', '#008300', '#e34948', '#0e8a96', '#9a6b2f'];

let data = null;
const st = { view: 'leads', project: '', q: '', status: '', lead: '', facets: {}, sort: '', shown: { ...STEP }, allChips: false };
const CHIPS = 8;  // project chips shown before "+ N more"

const fmtDate = (d) => {
  if (!d) return '';
  const t = new Date(d + 'T12:00');
  const days = Math.round((Date.now() - t) / 864e5);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 7) return days + ' days ago';
  return t.toLocaleDateString(window.hubLocale || 'en-GB', { day: 'numeric', month: 'short', ...(t.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) });
};
const pname = (id) => (data.projects.find((p) => p.id === id) || {}).name || id;
const pcolor = (id) => {  // a fixed colour per project: the first ten from the palette, then spread round the wheel
  const i = Math.max(0, data.projects.findIndex((p) => p.id === id));
  return i < PCOL.length ? PCOL[i] : `hsl(${Math.round((i * 137.5) % 360)} 55% 42%)`;
};
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');

function readHash() {
  const [v, qs] = location.hash.slice(1).split('?');
  st.view = v === 'research' ? 'research' : 'leads';
  const p = new URLSearchParams(qs || '');
  st.project = p.get('p') || '';
  st.status = p.get('s') || '';
  st.lead = p.get('l') || '';
  st.sort = p.get('o') || '';
  st.facets = Object.fromEntries((p.get('f') || '').split('|').filter(Boolean).map((x) => x.split('~')));
}
function writeHash() {
  const p = new URLSearchParams();
  if (st.project) p.set('p', st.project);
  if (st.status) p.set('s', st.status);
  const f = Object.entries(st.facets).filter(([, v]) => v).map(([k, v]) => k + '~' + v).join('|');
  if (f) p.set('f', f);
  if (st.sort) p.set('o', st.sort);
  if (st.lead) p.set('l', st.lead);
  const h = '#' + st.view + (p.toString() ? '?' + p : '');
  if (location.hash !== h) history.replaceState(null, '', h);
}

function statusBar(rows, big) {
  const n = rows.length || 1;
  const counts = STATUS.map(([k]) => [k, rows.filter((r) => r.status === k).length]).filter(([, c]) => c);
  return h('div', { class: 'sbar' + (big ? ' big' : ''), role: 'img', 'aria-label': counts.map(([k, c]) => `${c} ${SWORD[k]}`).join(', ') },
    counts.map(([k, c]) => h('i', { style: `flex:${c / n};background:${SCOLOR[k]}`, title: `${c} ${SWORD[k].toLowerCase()}` })));
}

function legend(rows) {
  return h('div', { class: 'legend' }, STATUS.filter(([k]) => rows.some((r) => r.status === k)).map(([k, c, w]) =>
    h('button', { type: 'button', class: 'lg' + (st.status === k ? ' on' : ''), onclick: () => { st.status = st.status === k ? '' : k; st.shown.leads = STEP.leads; render(); } },
      h('i', { style: `background:${c}` }), w, h('b', null, String(rows.filter((r) => r.status === k).length)))));
}

function chips(items, current, onpick, total) {
  const fold = !st.allChips && items.length > CHIPS + 4;  // a short list shows whole
  const rest = items.length - CHIPS;
  if (fold) items = items.filter((x, i) => i < CHIPS || x[0] === current);
  return h('div', { class: 'chips', role: 'tablist' },
    h('button', { type: 'button', class: 'chip' + (current ? '' : ' on'), onclick: () => onpick('') }, 'All ', h('b', null, String(total))),
    items.map(([id, n]) => h('button', { type: 'button', class: 'chip' + (current === id ? ' on' : ''), onclick: () => onpick(id) },
      h('i', { class: 'dot', style: `background:${pcolor(id)}` }), pname(id), ' ', h('b', null, String(n)))),
    fold ? h('button', { type: 'button', class: 'chip more-chips', onclick: () => { st.allChips = true; render(); } }, `+ ${rest} more`) : null);
}

function search(placeholder) {
  const inp = h('input', { type: 'search', id: 'q', placeholder, value: st.q, autocomplete: 'off', 'aria-label': placeholder });
  inp.addEventListener('input', () => { st.q = inp.value; st.shown = { ...STEP }; renderList(); if (st.view === 'leads') refreshFacets(); });
  return inp;
}

function more(total, key) {
  if (st.shown[key] >= total) return null;
  return h('button', { type: 'button', class: 'more', onclick: () => { st.shown[key] += STEP[key] * 2; renderList(); } },
    `Show more (${total - st.shown[key]} left)`);
}

// ------------------------------------------------------------ leads

const SRANK = Object.fromEntries(STATUS.map(([k], i) => [k, i]));
// filters: what a list's reader tags each lead with (r.facets), plus chance, type and town where they split the group
const FACET_IC = { State: 'send', Posted: 'clock', Age: 'clock', AI: 'spark', Where: 'pin', Field: 'briefcase', 'Open role': 'briefcase', Chance: 'target', Type: 'building', Town: 'pin' };
function facetsOf(r) {
  const f = { State: [bandOf(r)[1]], ...(r.facets || {}) };
  if (r.chance && !f.Chance) f.Chance = [(CHANCE[r.chance] || [r.chance])[0] + ' chance'];
  if (r.segment && !f.Type) f.Type = [r.segment];
  const town = (r.town || '').split(/[·,]/)[0].trim();
  if (town && !f.Where && !f.Town) f.Town = [town];
  if ('posted' in r) { if (r.posted) f.Posted = [r.posted === 'unknown' ? 'Date unknown' : postedAge(r.posted)]; }
  else if (r.touched && !f.Age) f.Age = [ageOf(r.touched)];
  return f;
}
// a job's ad: how long ago it went up
const POSTED = ['This week', 'This month', '1-3 months', 'Older', 'Date unknown'];
function postedAge(when) {
  const days = (Date.now() - new Date(when)) / 864e5;
  return POSTED[days < 7 ? 0 : days < 31 ? 1 : days < 92 ? 2 : 3];
}
// how long since anything happened on the lead (found, posted, written): newest first in the filter
const AGES = ['Today', 'This week', 'This month', 'Older'];
function ageOf(when) {
  const days = (Date.now() - new Date(when)) / 864e5;
  return AGES[days < 1 ? 0 : days < 7 ? 1 : days < 31 ? 2 : 3];
}
// filters with a natural order show it (best or newest first); the rest go by how many leads they hold
const ORDER = {
  get Age() { return AGES; }, get Posted() { return POSTED; },
  get State() { return [...STATUS.map(([k]) => (BAND[k] || [0, SWORD[k]])[1]), "Ad closed: don't send", 'Ad closed: skip it']; },
  get AI() { return [...Object.values(AI).map((x) => x[1]), 'Not rated']; }, Chance: ['High chance', 'Medium chance', 'Low chance'] };
function facetList(rows) {  // [name, [[value, count]...]] for filters that split these rows into 2-14 useful parts
  const out = {};
  for (const r of rows) for (const [k, vs] of Object.entries(facetsOf(r))) for (const v of [].concat(vs)) {
    out[k] = out[k] || {};
    out[k][v] = (out[k][v] || 0) + 1;
  }
  return Object.entries(out).map(([k, m]) => [k, Object.entries(m).sort((a, b) => ORDER[k] ? ORDER[k].indexOf(a[0]) - ORDER[k].indexOf(b[0]) : b[1] - a[1])])
    .filter(([k, vals]) => st.facets[k] || (vals.length >= 2 && vals.length <= 14 && vals[0][1] < rows.length));
}
const facetOk = (r, skip) => Object.entries(st.facets).every(([k, v]) => k === skip || !v || [].concat(facetsOf(r)[k] || []).includes(v));

function baseRows() {
  const q = norm(st.q).trim();
  return data.leads.filter((r) => (!st.project || r.project === st.project) && (!st.status || r.status === st.status) &&
    (!q || norm([r.name, r.town, r.segment, r.know, r.next].join(' ')).includes(q)));
}
function leadRows() {
  const rows = baseRows().filter((r) => facetOk(r));
  const by = (SORTS.find(([k]) => k === st.sort) || [])[2];
  if (by) rows.sort((a, b) => by(a, b) || (b.touched || '').localeCompare(a.touched || ''));
  // one group: the threads in motion first (a sent or drafted one above the not-yet-contacted), newest first within
  else if (st.project) rows.sort((a, b) => (SRANK[a.status] ?? 99) - (SRANK[b.status] ?? 99) || (b.touched || '').localeCompare(a.touched || ''));
  return rows;
}
// how a list can be ordered; a choice shows only when some row has what it sorts by
const dated = (x) => (x && x !== 'unknown' ? x : '');
const CRANK = (r) => ({ high: 3, mid: 2, low: 1 })[r.chance] || 0;
const SORTS = [
  ['', 'Next move first', null, () => true],
  ['new', 'Newest ad', (a, b) => dated(b.posted).localeCompare(dated(a.posted)), (rows) => rows.some((r) => dated(r.posted))],
  ['recent', 'Recently active', (a, b) => (b.touched || '').localeCompare(a.touched || ''), () => true],
  ['chance', 'Best chance', (a, b) => CRANK(b) - CRANK(a), (rows) => rows.some((r) => r.chance)],
  ['ai', 'Most AI', (a, b) => ((AI[b.ai] || [-1])[0]) - ((AI[a.ai] || [-1])[0]), (rows) => rows.some((r) => r.ai)],
];
const optOn = (key, v) => key === '_sort' ? (st.sort || '_default') === v : (st.facets[key] || '') === v;
// filters as one compact row of dropdowns, each showing what is picked; a tap opens its options with counts
function fselect(key, label, icon, current, glyph, options, pick) {
  const open = st.openFacet === key;
  const btn = h('button', { type: 'button', class: 'fbtn' + (current ? ' on' : '') + (open ? ' open' : ''), 'aria-expanded': String(open),
    onclick: (e) => { e.stopPropagation(); st.openFacet = open ? '' : key; refreshFacets(); } },
  ic(icon), h('span', { class: 'fl' }, label), current ? h('span', { class: 'fval' }, glyph, current) : h('span', { class: 'fany' }, 'All'), ic('down'));
  const pop = open ? h('div', { class: 'fpop', role: 'listbox', onclick: (e) => e.stopPropagation() },
    options.map(([v, word, g, n, off]) => h('button', { type: 'button', class: 'fopt' + (optOn(key, v) ? ' on' : ''), disabled: !!off,
      onclick: () => { st.openFacet = ''; pick(v); st.shown = { ...STEP }; renderList(); refreshFacets(); } },
    g || h('span', { class: 'ic' }), h('span', { class: 'fw' }, word), n == null ? null : h('b', null, String(n))))) : null;
  if (pop) requestAnimationFrame(() => {  // keep the open list on screen near the right edge
    const r = pop.getBoundingClientRect(), over = r.right - (window.innerWidth - 12);
    if (over > 0) pop.style.left = `${-over}px`;
  });
  return h('div', { class: 'fsel' }, btn, pop);
}
function facetBar() {
  const base = baseRows();
  if (!base.length) return null;
  const list = facetList(base);
  const sorts = SORTS.filter(([, , , show]) => show(base));
  const sortWord = (sorts.find(([k]) => k === st.sort) || sorts[0])[1];
  return h('div', { class: 'facets' },
    fselect('_sort', 'Sort', 'sort', st.sort ? sortWord : '', null, sorts.map(([k, word]) => [k || '_default', word, null, null]),
      (v) => { st.sort = v === '_default' ? '' : v; }),
    list.map(([k, vals]) => {
      const here = base.filter((r) => facetOk(r, k));  // counts as they would be with this filter changed
      const cur = st.facets[k] || '';
      return fselect(k, k, FACET_IC[k] || 'target', cur, cur ? valueGlyph(k, cur) : null,
        [['', 'All', null, here.length], ...vals.map(([v]) => {
          const n = here.filter((r) => [].concat(facetsOf(r)[k] || []).includes(v)).length;
          return [v, v, valueGlyph(k, v), n, !n && cur !== v];
        })],
        (v) => { st.facets = { ...st.facets, [k]: v }; });
    }),
    Object.values(st.facets).some(Boolean) || st.sort ? h('button', { type: 'button', class: 'fclear',
      onclick: () => { st.facets = {}; st.sort = ''; st.openFacet = ''; st.shown = { ...STEP }; renderList(); refreshFacets(); } }, ic('x'), 'Clear') : null);
}
document.addEventListener('click', () => { if (st.openFacet) { st.openFacet = ''; refreshFacets(); } });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && st.openFacet) { st.openFacet = ''; refreshFacets(); } });
function refreshFacets() {
  const old = document.querySelector('.facets');
  const nu = facetBar();
  if (old && nu) old.replaceWith(nu);
  writeHash();
}

// the open roles of a lead, those in the chosen field first (a firm hiring a developer and a sales assistant shows
// only the developer under IT)
function openRoles(r) {
  const rows = ((r.detail || []).find((x) => /open roles/i.test(x.title || '')) || { rows: [] }).rows;
  const f = st.facets.Field, a = st.facets.AI;
  return rows.filter((x) => x.live !== 'closed' && (!f || !x.field || x.field === f) && (!a || !x.ai || (AI[x.ai] || [])[1] === a));
}
const bestAi = (rows) => rows.map((x) => x.ai).filter((x) => AI[x]).sort((a, b) => AI[b][0] - AI[a][0])[0] || '';

function leadCard(r) {
  const src = r.source || {};
  const links = [];
  if (src.url) links.push(h('a', { href: src.url, target: '_blank', rel: 'noopener' }, src.label || 'Source'));
  else if (src.link) links.push(h('a', { href: src.link }, src.label || 'Source'));
  if (src.url && src.link) links.push(h('a', { href: src.link, class: 'sub' }, src.path_label || 'notes'));
  if (r.web) links.push(h('a', { href: r.web, target: '_blank', rel: 'noopener', class: 'sub' }, r.web.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')));
  const matching = openRoles(r);
  const roles = matching.length;
  const filtered = st.facets.Field || st.facets.AI;
  const segment = filtered && matching.length ? matching[0].text : r.segment;
  const ai = filtered ? bestAi(matching) || r.ai : r.ai;
  // a written draft is the next step itself: the state says it, so the card drops the 'Next' line
  const mail = (r.detail || []).some((x) => x.mail) && r.status === 'drafted';
  return h('article', { class: 'lead', style: `--c:${pcolor(r.project)}`, tabindex: '0', role: 'button', 'aria-label': `${r.name}: open the detail`,
    onclick: (e) => { if (!e.target.closest('a')) openLead(r); },
    onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) openLead(r); } },
    band(r),
    h('div', { class: 'lead-top' },
      avatar(r.name, r.web),
      h('div', { class: 'lead-id' },
        h('h3', null, r.name),
        h('div', { class: 'where' },
          st.project && !r.group ? null : h('span', null, ic('building'), r.group || pname(r.project)),
          segment ? h('span', null, ic((r.facets || {}).Field ? fieldIc((openRoles(r).find((x) => x.text.startsWith(segment)) || matching[0] || {}).field || ((r.facets || {}).Field || [])[0]) : 'briefcase'), segment) : null,
          r.town ? h('span', null, ic(placeIc(r.town)), r.town) : null)),
    ),
    r.chance || r.person || roles || r.ai || r.posted ? h('div', { class: 'badges' },
      r.chance ? chanceChip(r.chance) : null,
      ai ? aiChip(ai) : null,
      roles ? h('span', { class: 'bdg' }, ic('briefcase'), (roles === 1 ? '1 open role' : roles + ' open roles') + (filtered ? ' that match' : '')) : null,
      r.person && !/^not found$/i.test(r.person) ? h('span', { class: 'bdg' }, ic('user'), r.person) : null,
      r.posted && r.posted !== 'unknown' ? h('span', { class: 'bdg' + (postedAge(r.posted) === POSTED[0] ? ' fresh' : ''), title: r.posted }, ic('clock'), 'Posted ' + fmtDate(r.posted)) : null) : null,
    r.know ? h('p', { class: 'know' }, r.know) : null,
    movesFor(r, 'card'),
    // the move buttons are the answer to 'Next', so a card with them drops the line (the detail keeps it)
    mail || r.ad === 'closed' || (r.moves || []).length || (r.agent && r.agent.status !== 'done') ? null : h('div', { class: 'next' + (r.next ? '' : ' none') }, h('span', null, 'Next'), r.next || (r.status === 'dropped' ? 'Nothing: dropped' : 'Not decided yet')),
    h('div', { class: 'foot' }, h('span', { class: 'links' }, links), r.touched ? h('span', { class: 'when', title: r.touched }, fmtDate(r.touched)) : null));
}

// ------------------------------------------------------------ one lead, everything we know (a tap on its card)

const leadId = (r) => norm(r.project + '-' + r.name).replace(/[^a-z0-9]+/g, '-');
function openLead(r, quiet) {
  closeLead(true);
  st.lead = leadId(r);
  if (!quiet) writeHash();
  const close = () => { closeLead(); };
  const ext = (url) => /^https?:/.test(url) ? { target: '_blank', rel: 'noopener' } : {};
  const link = (text, url) => url ? h('a', { href: url, ...ext(url) }, text) : text;
  const det = r.detail || [];
  const rows = det.flatMap((x) => x.rows || []);
  // the moves a tap away: write, call, open the site or the careers page
  const email = rows.find((x) => /^mailto:/.test(x.url || ''));
  const phone = rows.find((x) => /^tel:/.test(x.url || ''));
  const careers = rows.find((x) => /career/i.test(x.label || '') && x.url);
  const tiles = [
    email && ['mail', 'Write', email.text, email.url],
    phone && ['phone', 'Call', phone.text.split(/[,(]/)[0], phone.url],
    r.web && ['globe', 'Website', domainOf(r.web), r.web],
    careers && ['briefcase', 'Careers', domainOf(careers.url), careers.url],
  ].filter(Boolean);
  const section = (sec) => {
    const icon = (SECTION_IC.find(([re]) => re.test(sec.title || '')) || [])[1];
    const head = h('h4', null, ic(icon), sec.title);
    if (/open roles/i.test(sec.title || '')) {  // open roles: one card each, with how well it fits
      const keep = new Set(openRoles(r));
      const sorted = [...sec.rows].sort((a, b) => keep.has(b) - keep.has(a) || ((AI[b.ai] || [0])[0] - (AI[a.ai] || [0])[0]));
      return h('section', { class: 'ld-sec' }, head, h('div', { class: 'roles' }, sorted.map((x) => h(x.url ? 'a' : 'div', { class: 'role' + (keep.has(x) ? '' : ' other'), ...(x.url ? { href: x.url, ...ext(x.url) } : {}) },
        h('span', { class: 'role-t' }, x.text),
        h('span', { class: 'role-m' }, x.field ? h('span', { class: 'fld' }, x.field) : null, x.ai ? aiChip(x.ai) : null, x.fit ? h('span', { class: 'fitm' }, meter(x.fit), 'fit') : null,
          x.live === 'closed' ? h('span', { class: 'adclosed' }, ic('x'), 'Closed' + (x.closed_since ? ' ' + fmtDate(x.closed_since) : '')) : null,
          x.live === 'open' && x.checked ? h('span', { class: 'checked', title: 'The morning ad check found it still open' }, ic('refresh'), 'Open, checked ' + fmtDate(x.checked)) : null,
          x.posted ? h('span', { class: 'posted' + (postedAge(x.posted) === POSTED[0] ? ' fresh' : ''), title: x.posted }, ic('clock'), 'Posted ' + fmtDate(x.posted)) : null, x.note || ''),
        x.ai_quote ? h('span', { class: 'role-q' }, x.ai_quote) : null,
        x.url ? ic('arrow', 'go') : null))));
    }
    if (sec.rows.every((x) => x.url && !x.label)) {  // sources: chips with each site's own icon
      return h('section', { class: 'ld-sec' }, head, h('div', { class: 'srcs' }, sec.rows.map((x) =>
        h('a', { class: 'src', href: x.url, ...ext(x.url) }, avatar(domainOf(x.url), x.url), domainOf(x.url) || x.text))));
    }
    return h('section', { class: 'ld-sec' }, head,
      h('dl', null, sec.rows.map((x) => h('div', { class: 'ld-row' + (x.label ? '' : ' nolabel') },
        x.label ? h('dt', null, labelIc(x.label), x.label) : null,
        h('dd', null, link(x.text, x.url), x.note ? h('small', null, x.note) : null)))));
  };
  const src = r.source || {};
  const panel = h('div', { class: 'drawer-panel lead-detail', role: 'dialog', 'aria-modal': 'true', 'aria-label': r.name, tabindex: '-1', style: `--c:${pcolor(r.project)}` },
    h('header', { class: 'ld-hero' },
      h('button', { type: 'button', class: 'ld-close', 'aria-label': 'Close', onclick: close }, ic('x')),
      avatar(r.name, r.web, true),
      h('h2', null, r.name),
      h('div', { class: 'where' }, [r.segment, r.town].filter(Boolean).map((x, i) => h('span', null, i ? ic('pin') : ic('briefcase'), x))),
      h('div', { class: 'tags' },
        h('span', { class: 'st', style: `--sc:${SCOLOR[r.status] || 'var(--dim)'}` }, SWORD[r.status] || r.status),
        r.chance ? chanceChip(r.chance) : null,
        r.ai ? aiChip(r.ai) : null,
        h('span', { class: 'grp' }, h('i', { class: 'dot', style: `background:${pcolor(r.project)}` }), r.group || pname(r.project)),
        r.touched ? h('span', { class: 'when' }, fmtDate(r.touched)) : null)),
    tiles.length ? h('div', { class: 'tiles' }, tiles.map(([icn, k, v, url]) => h('a', { class: 'tile', href: url, ...ext(url) }, ic(icn), h('b', null, k), h('small', null, v)))) : null,
    h('div', { class: 'ld-moves' }, movesFor(r, 'detail')),
    h('div', { class: 'ld-next' + (r.next ? '' : ' none') }, ic('send'), h('div', null, h('span', null, 'Next'), r.next || (r.status === 'dropped' ? 'Nothing: dropped' : 'Not decided yet'))),
    r.ai && r.ai_why ? h('div', { class: 'ld-why ld-ai' }, ic('spark'), h('div', null, h('b', null, `AI in this job: ${AI[r.ai][1]}`), h('p', null, r.ai_why))) : null,
    ...det.filter((x) => x.mail).map((sec) => mailBlock(sec.title, sec.mail, r)),
    r.chance_why ? h('div', { class: 'ld-why' }, ic('target'), h('div', null, h('b', null, `Why the chance is ${(CHANCE[r.chance] || [r.chance])[0].toLowerCase()}`), h('p', null, r.chance_why))) : null,
    r.know && !det.length ? h('p', { class: 'know' }, r.know) : null,
    h('div', { class: 'ld-grid' }, det.filter((x) => !x.mail && (x.rows || []).length).map(section)),
    src.url || src.link ? h('div', { class: 'ld-links' },
      src.url ? link(src.label || 'Source', src.url) : null,
      src.link ? link(src.url ? (src.path_label || 'Notes') : (src.label || 'Notes'), src.link) : null) : null);
  const box = h('div', { class: 'drawer', id: 'lead-drawer' }, h('div', { class: 'drawer-backdrop', onclick: close }), panel);
  document.body.append(box);
  panel.focus();
}

// a mail ready to go: who, subject, the text as he will send it, and one tap to send or copy it
function mailBlock(title, m, r) {
  const text = (m.body || '').replace(/\*\*([^*]+)\*\*/g, '$1');
  const formUrl = ((m.where || '').match(/https?:\/\/\S+/) || [])[0];
  const copy = h('button', { type: 'button' }, ic('copy'), 'Copy text');
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText((m.subject ? m.subject + '\n\n' : '') + text); copy.lastChild.textContent = 'Copied'; }
    catch { copy.lastChild.textContent = 'Copy failed'; }
    setTimeout(() => { copy.lastChild.textContent = 'Copy text'; }, 1800);
  };
  const mailto = m.to ? 'mailto:' + m.to.split(/[\s;,]/)[0] + '?subject=' + encodeURIComponent(m.subject || '') + '&body=' + encodeURIComponent(text) : '';
  const meta = [['To', m.to], ['Subject', m.subject], ['Attach', (m.attach || '').split('/').pop()], ['Send via', formUrl ? null : m.where]].filter(([, v]) => v);
  return h('section', { class: 'ld-sec ld-mail' },
    h('h4', null, ic('mail'), title),
    h('div', { class: 'mail' },
      meta.length ? h('dl', { class: 'mail-meta' }, meta.map(([k, v]) => h('div', null, h('dt', null, k), h('dd', null, v)))) : null,
      h('div', { class: 'mail-body' }, text)),
    h('div', { class: 'mail-acts' },
      mailto ? h('button', { type: 'button', class: 'primary', onclick: () => { location.href = mailto; } }, ic('send'), 'Open in Mail') : null,
      formUrl ? h('button', { type: 'button', class: 'primary', onclick: () => open(formUrl, '_blank', 'noopener') }, ic('send'), 'Open the form') : null,
      copy),
    r ? h('div', { class: 'mail-edit' }, movesFor(r, 'mail')) : null);
}

function closeLead(keepHash) {
  const d = document.getElementById('lead-drawer');
  if (d) d.remove();
  if (!keepHash && st.lead) { st.lead = ''; writeHash(); }
}
addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLead(); });

function projectCards() {
  const ps = data.projects.filter((p) => p.leads);
  return h('div', { class: 'pcards' }, ps.map((p) => {
    const rows = data.leads.filter((r) => r.project === p.id);
    const moving = rows.filter((r) => !['not contacted', 'dropped', 'rejected'].includes(r.status)).length;
    return h('button', { type: 'button', class: 'pcard' + (st.project === p.id ? ' on' : ''), style: `--c:${pcolor(p.id)}`,
      onclick: () => { st.project = st.project === p.id ? '' : p.id; st.status = ''; st.facets = {}; st.shown = { ...STEP }; render(); } },
      h('span', { class: 'pn' }, p.name),
      h('span', { class: 'big' }, String(p.leads)),
      statusBar(rows),
      h('span', { class: 'ps' }, moving ? `${moving} in motion` : 'not contacted', h('em', null, fmtDate(p.touched))));
  }));
}

function waitingCards() {  // open decision cards a lead group waits on: the next move is his
  const rows = (data.waiting || []).filter((q) => !st.project || q.project === st.project);
  if (!rows.length) return null;
  return h('section', { class: 'waits' }, rows.map((q) => h('a', { class: 'wait', href: `/decisions#/q/${q.id}`, style: `--c:${pcolor(q.project)}` },
    h('span', { class: 'wk' }, 'Waiting on you'),
    h('span', { class: 'wq' }, q.question),
    h('span', { class: 'wp' }, pname(q.project)))));
}

// the jobs that keep this list fresh, with when they last ran and run next: nothing here updates unseen
const when = (t, ahead) => {
  if (!t) return ahead ? 'not scheduled' : 'never seen';
  const d = new Date(t * 1000), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const day = Math.round((new Date(d.toDateString()) - new Date(now.toDateString())) / 864e5);
  const mins = Math.round(Math.abs(now - d) / 6e4);
  if (mins < 60) return ahead ? `in ${mins} min` : `${mins} min ago`;
  return day === 0 ? `today ${hm}` : day === 1 ? `tomorrow ${hm}` : day === -1 ? `yesterday ${hm}` : fmtDate(d.toISOString());
};
const FEED_WORD = { ok: ['OK', 'var(--good)'], running: ['Running now', 'var(--accent)'], failed: ['Failed', 'var(--bad)'],
  overdue: ['Overdue', 'var(--warn)'], gone: ['Gone', 'var(--bad)'], never: ['Never ran', 'var(--dim)'], unknown: ['Not seen on any machine', 'var(--dim)'] };
function feedStrip() {
  const rows = (data.feeds || []).filter((f) => f.project === st.project);
  if (!rows.length) return null;
  const newest = Math.max(...rows.map((f) => f.last_run || 0));
  return h('section', { class: 'feeds' },
    h('div', { class: 'feeds-h' }, ic('refresh'), h('b', null, 'Kept fresh by'), newest ? h('span', null, `updated ${when(newest)}`) : null,
      h('a', { href: '/machines#/jobs' }, 'All jobs', ic('arrow'))),
    h('div', { class: 'feeds-l' }, rows.map((f) => {
      const [word, col] = FEED_WORD[f.status] || [f.status || 'Unknown', 'var(--dim)'];
      return h('a', { class: 'feed', style: `--fc:${col}`, href: f.machine ? `/machines#/m/${encodeURIComponent(f.machine)}` : '/machines#/jobs', title: f.job },
        h('span', { class: 'feed-t' }, h('i'), f.job.replace(/-/g, ' '), h('em', null, word)),
        h('span', { class: 'feed-w' }, f.what),
        h('span', { class: 'feed-r' }, ic('clock'), `Ran ${when(f.last_run)}`, h('span', null, '·'), `next ${when(f.next_run, true)}`));
    })));
}

// ------------------------------------------------------------ next moves
// What the data says to do next (the list's reader offers them: no draft -> Write the draft, closed ad -> Drop it…).
// A tap asks once, with room for a note; the hub then files it for the task agent, and the card shows that agent
// live (queued, working with its session, needs you, done) instead of the buttons.
const leadKey = (r) => r.project + ' / ' + r.name;
function agentLine(a) {
  const link = `/#/task/${a.task}`;
  if (a.waiting) return h('a', { class: 'agent wait', href: link }, ic('bot'), h('span', null, h('b', null, 'Needs you'), a.note.replace(/^⏳\s*(Needs you:)?\s*/, '')), ic('arrow'));
  if (a.status === 'done') return h('a', { class: 'agent done', href: link }, ic('check'), h('span', null, h('b', null, 'Agent done'), a.note.replace(/^✅\s*/, '') || a.title), ic('arrow'));
  if (a.status === 'doing') {
    const sx = a.session;
    return h('a', { class: 'agent live', href: link }, h('i', { class: 'pulse' }), h('span', null, h('b', null, 'Agent working'),
      sx ? (sx.doing && !/^\$|^[\w-]+\(/.test(sx.doing) ? sx.doing : a.title) + (sx.machine ? ` · on ${sx.machine}` : '') : a.title), ic('arrow'));
  }
  if (a.handed) return h('a', { class: 'agent queued', href: link }, ic('bot'), h('span', null, h('b', null, 'Queued'), 'the agent starts within a minute'), ic('arrow'));
  return h('a', { class: 'agent wait', href: link }, ic('bot'), h('span', null, h('b', null, 'Back with you'), a.note || a.title), ic('arrow'));
}
function movesFor(r, where) {
  if (r.agent && r.agent.status !== 'done') return agentLine(r.agent);
  // a move the agent just finished stays hidden until the lead's files show its result
  const moves = (r.moves || []).filter((m) => !(r.agent && r.agent.fresh && r.agent.move === m.id) && (where !== 'mail' || m.id === 'rewrite')
    && !(where === 'detail' && m.id === 'rewrite' && (r.detail || []).some((x) => x.mail)));  // the draft's own box offers it
  const done = r.agent ? agentLine(r.agent) : null;
  if (!moves.length) return done;
  const open = st.moving && st.moving.key === leadKey(r) && st.moving.where === where ? moves.find((m) => m.id === st.moving.move) : null;
  if (open) {
    const note = h('textarea', { class: 'mv-note', rows: '2', placeholder: open.ask || 'Anything the agent should know? (optional)',
      oninput: (e) => { st.moving.note = e.target.value; } });
    note.value = st.moving.note || '';
    const go = h('button', { type: 'button', class: 'mv go' }, ic('bot'), 'Hand to the agent');
    go.onclick = async (e) => {
      e.stopPropagation(); go.disabled = true; go.lastChild.textContent = 'Handing over…';
      try {
        const res = await fetch('/api/outreach/move', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project: r.project, name: r.name, move: open.id,
            note: (st.moving.quote ? `Change this part of the draft:\n> ${st.moving.quote.replace(/\n/g, '\n> ')}\n\n` : '') + note.value }) });
        const out = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(out.error || 'The hub answered ' + res.status);
        st.moving = null; painted = ''; await load(); rerender();
      } catch (err) { go.disabled = false; go.lastChild.textContent = 'Try again'; go.title = err.message; showError(err); }
    };
    return h('div', { class: 'mv-ask', onclick: (e) => e.stopPropagation() },
      h('div', { class: 'mv-q' }, ic(open.icon || 'bot'), h('b', null, st.moving.quote ? 'Change this part' : open.label), h('span', null, 'An agent does it now and shows here; nothing is sent.')),
      st.moving.quote ? h('blockquote', { class: 'mv-quote' }, st.moving.quote) : null,
      note, h('div', { class: 'mv-row' }, go, h('button', { type: 'button', class: 'mv ghost', onclick: () => { st.moving = null; rerender(); } }, 'Cancel')));
  }
  return h('div', { class: 'moves', onclick: (e) => e.stopPropagation() }, done,
    moves.map((m) => h('button', { type: 'button', class: 'mv' + (m.primary ? ' go' : ''),
      onclick: () => { st.moving = { key: leadKey(r), move: m.id, where }; rerender(); } }, ic(m.icon || 'bot'),
      where === 'mail' ? 'Change something…' : m.label)),
    where === 'mail' ? h('span', { class: 'mv-hint' }, 'or select a part of the text') : null);
}
function rerender() {
  renderList();
  const d = document.getElementById('lead-drawer');
  const r = d && st.lead && data.leads.find((x) => leadId(x) === st.lead);
  if (!r) return;
  const slot = d.querySelector('.ld-moves');
  if (slot) slot.replaceChildren(movesFor(r, 'detail') || '');
  d.querySelectorAll('.mail-edit').forEach((x) => x.replaceChildren(movesFor(r, 'mail') || ''));
  const ask = d.querySelector('.mail-edit .mv-note');
  if (ask && st.moving && st.moving.where === 'mail' && st.moving.focus) { st.moving.focus = false; ask.focus(); ask.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
}
// select a part of a draft: a chip offers to have the agent change just that part
let selChip = null;
function hideSelChip() { if (selChip) { selChip.remove(); selChip = null; } }
document.addEventListener('selectionchange', () => {
  const sel = document.getSelection();
  const text = sel && !sel.isCollapsed ? sel.toString().trim() : '';
  const body = text && sel.anchorNode && sel.anchorNode.parentElement && sel.anchorNode.parentElement.closest('.mail-body');
  const r = body && st.lead && data && data.leads.find((x) => leadId(x) === st.lead);
  if (!r || !(r.moves || []).some((m) => m.id === 'rewrite') || (r.agent && r.agent.status !== 'done')) { hideSelChip(); return; }
  const box = sel.getRangeAt(0).getBoundingClientRect();
  hideSelChip();
  selChip = h('button', { type: 'button', class: 'sel-chip', style: `left:${Math.max(12, Math.min(innerWidth - 190, box.left + box.width / 2 - 85))}px;top:${Math.max(8, box.top - 46)}px`,
    onmousedown: (e) => e.preventDefault(),
    onclick: () => { st.moving = { key: leadKey(r), move: 'rewrite', where: 'mail', quote: text.slice(0, 1200), focus: true }; hideSelChip(); rerender(); } },
  ic('pen'), 'Change this part');
  document.body.append(selChip);
});
addEventListener('scroll', hideSelChip, true);
let polling = 0;
function keepWatching() {  // while an agent works a lead, the page refreshes itself
  const busy = (data.leads || []).some((r) => r.agent && r.agent.status !== 'done');
  if (busy && !polling) polling = setInterval(() => { if (!document.hidden) load(); }, 15000);
  if (!busy && polling) { clearInterval(polling); polling = 0; }
}

function renderLeads(v) {
  const all = data.leads;
  const moving = all.filter((r) => !['not contacted', 'dropped', 'rejected'].includes(r.status)).length;
  const pick = st.project ? all.filter((r) => r.project === st.project) : all;
  v.append(
    h('section', { class: 'hero' },
      h('div', { class: 'kpis' },
        h('div', { class: 'kpi' }, h('span', { class: 'v' }, String(all.length)), h('span', { class: 'k' }, 'businesses researched')),
        h('div', { class: 'kpi' }, h('span', { class: 'v' }, String(moving)), h('span', { class: 'k' }, 'drafted or contacted')),
        h('div', { class: 'kpi' }, h('span', { class: 'v' }, String(data.projects.filter((p) => p.leads).length)), h('span', { class: 'k' }, 'projects'))),
      h('div', { class: 'stack' }, statusBar(pick, true), legend(pick))),
    waitingCards(),
    feedStrip(),
    projectCards(),
    h('div', { class: 'filters' },
      chips(data.projects.filter((p) => p.leads).map((p) => [p.id, p.leads]), st.project,
        (id) => { st.project = id; st.status = ''; st.facets = {}; st.shown = { ...STEP }; render(); }, all.length),
      search('Search a name, town, what we know…')),
    facetBar() || h('div', { class: 'facets', hidden: true }),
    h('div', { id: 'list' }));
  renderList();
}

// ------------------------------------------------------------ research

function researchRows() {
  const q = norm(st.q).trim();
  return data.research.filter((r) => (!st.project || r.project === st.project) &&
    (!q || norm([r.title, pname(r.project), r.kind].join(' ')).includes(q)));
}

function researchRow(r) {
  const art = r.where === 'artifact';
  return h('a', { class: 'doc-row', href: r.link, ...(art || /^https?:/.test(r.link) ? { target: '_blank', rel: 'noopener' } : {}), style: `--c:${pcolor(r.project)}` },
    h('span', { class: 'ic ' + (art ? 'art' : 'file'), title: art ? 'Claude artifact' : 'File' }, art ? '◆' : '¶'),
    h('span', { class: 'dt' },
      h('span', { class: 'tt' }, r.title),
      h('span', { class: 'mt' }, [st.project ? null : pname(r.project), KIND[r.kind] || r.kind, art ? 'Artifact' : (/github\.com/.test(r.link) ? 'GitHub' : 'File')].filter(Boolean).join(' · '))),
    h('span', { class: 'when', title: r.date || '' }, fmtDate(r.date)));
}

function renderResearch(v) {
  const all = data.research;
  const byP = {};
  for (const r of all) byP[r.project] = (byP[r.project] || 0) + 1;
  const arts = all.filter((r) => r.where === 'artifact').length;
  const month = all.filter((r) => r.date && Date.now() - new Date(r.date + 'T12:00') < 30 * 864e5).length;
  v.append(
    h('section', { class: 'hero' },
      h('div', { class: 'kpis' },
        h('div', { class: 'kpi' }, h('span', { class: 'v' }, String(all.length)), h('span', { class: 'k' }, 'reports and pages')),
        h('div', { class: 'kpi' }, h('span', { class: 'v' }, String(arts)), h('span', { class: 'k' }, 'Claude artifacts')),
        h('div', { class: 'kpi' }, h('span', { class: 'v' }, String(month)), h('span', { class: 'k' }, 'in the last 30 days')))),
    h('div', { class: 'filters' },
      chips(data.projects.filter((p) => byP[p.id]).map((p) => [p.id, byP[p.id]]).sort((a, b) => b[1] - a[1]), st.project,
        (id) => { st.project = id; st.shown = { ...STEP }; render(); }, all.length),
      search('Search a title…')),
    h('div', { id: 'list' }));
  renderList();
}

function renderList() {
  const box = $('list');
  if (!box) return;
  box.replaceChildren();
  if (st.view === 'leads') {
    const rows = leadRows();
    box.className = 'leads';
    if (!rows.length) return box.append(h('p', { class: 'empty' }, 'No lead matches.'));
    box.append(...rows.slice(0, st.shown.leads).map(leadCard));
    const m = more(rows.length, 'leads');
    if (m) box.append(m);
  } else {
    const rows = researchRows();
    box.className = 'docs';
    if (!rows.length) return box.append(h('p', { class: 'empty' }, 'Nothing matches.'));
    box.append(...rows.slice(0, st.shown.research).map(researchRow));
    const m = more(rows.length, 'research');
    if (m) box.append(m);
  }
}

function render() {
  if (!data) return;
  writeHash();
  $('n-leads').textContent = data.leads.length;
  $('n-research').textContent = data.research.length;
  document.querySelectorAll('.views a').forEach((a) => a.classList.toggle('on', a.dataset.v === st.view));
  const v = $('view');
  const keepFocus = document.activeElement && document.activeElement.id === 'q';
  v.replaceChildren();
  (st.view === 'leads' ? renderLeads : renderResearch)(v);
  if (keepFocus) { const q = $('q'); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  const want = st.lead && data.leads.find((r) => leadId(r) === st.lead);
  if (want && !document.getElementById('lead-drawer')) openLead(want, true);
  else if (!want) closeLead(true);
}

async function load() {
  try {
    const r = await fetch('/api/outreach', { cache: 'no-store' });
    if (!r.ok) throw new Error('The hub answered ' + r.status);
    const d = await r.json();
    $('error').hidden = true;
    const s = JSON.stringify(d);
    if (s === painted) { keepWatching(); return; }
    if (st.moving) { data = d; painted = s; snap.set('outreach', d); keepWatching(); return; }  // he is typing a note: redraw after
    painted = s;
    data = d;
    snap.set('outreach', d);
    try { localStorage.removeItem('outreach.snap'); } catch { /* blocked */ }  // the key before shared.js's snap
    render();
    rerender();  // an open lead detail shows the fresh agent state too
    keepWatching();
  } catch (e) {
    if (!data) showError(e);
  }
}

readHash();
data = snap.get('outreach');
if (!data) try { data = JSON.parse(localStorage.getItem('outreach.snap')); } catch { data = null; }
try { render(); painted = data ? JSON.stringify(data) : ''; } catch { data = null; painted = ''; }  // a snapshot that no longer fits waits for fresh data
load();
document.querySelectorAll('.views a').forEach((a) => a.addEventListener('click', (e) => {
  e.preventDefault();
  if (st.view === a.dataset.v) return;
  st.view = a.dataset.v; st.project = ''; st.status = ''; st.q = ''; st.shown = { ...STEP };
  render(); scrollTo(0, 0);
}));
// phones: Outreach is the last Home tab, scrolled into sight once the header has its theme button
addEventListener('load', () => { const t = document.querySelector('.top .tabs'); if (t) t.scrollLeft = t.scrollWidth; });
addEventListener('hashchange', () => { readHash(); render(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
