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
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
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
const chanceChip = (level) => h('span', { class: 'chance' }, meter(level), (CHANCE[level] || [level])[0] + ' chance');

const KIND = { research: 'Research', plan: 'Plan', draft: 'Draft', notes: 'Notes', other: 'Page' };
const PCOL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#4a3aa7', '#008300', '#e34948', '#0e8a96', '#9a6b2f'];

let data = null;
const st = { view: 'leads', project: '', q: '', status: '', lead: '', shown: { ...STEP }, allChips: false };
const CHIPS = 8;  // project chips shown before "+ N more"

const fmtDate = (d) => {
  if (!d) return '';
  const t = new Date(d + 'T12:00');
  const days = Math.round((Date.now() - t) / 864e5);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 7) return days + ' days ago';
  return t.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(t.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) });
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
}
function writeHash() {
  const p = new URLSearchParams();
  if (st.project) p.set('p', st.project);
  if (st.status) p.set('s', st.status);
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
  inp.addEventListener('input', () => { st.q = inp.value; st.shown = { ...STEP }; renderList(); });
  return inp;
}

function more(total, key) {
  if (st.shown[key] >= total) return null;
  return h('button', { type: 'button', class: 'more', onclick: () => { st.shown[key] += STEP[key] * 2; renderList(); } },
    `Show more (${total - st.shown[key]} left)`);
}

// ------------------------------------------------------------ leads

const SRANK = Object.fromEntries(STATUS.map(([k], i) => [k, i]));
function leadRows() {
  const q = norm(st.q).trim();
  const rows = data.leads.filter((r) => (!st.project || r.project === st.project) && (!st.status || r.status === st.status) &&
    (!q || norm([r.name, r.town, r.segment, r.know, r.next].join(' ')).includes(q)));
  // one group: the threads in motion first (a sent or drafted one above the not-yet-contacted), newest first within
  if (st.project) rows.sort((a, b) => (SRANK[a.status] ?? 99) - (SRANK[b.status] ?? 99) || (b.touched || '').localeCompare(a.touched || ''));
  return rows;
}

function leadCard(r) {
  const src = r.source || {};
  const links = [];
  if (src.url) links.push(h('a', { href: src.url, target: '_blank', rel: 'noopener' }, src.label || 'Source'));
  else if (src.link) links.push(h('a', { href: src.link }, src.label || 'Source'));
  if (src.url && src.link) links.push(h('a', { href: src.link, class: 'sub' }, src.path_label || 'notes'));
  if (r.web) links.push(h('a', { href: r.web, target: '_blank', rel: 'noopener', class: 'sub' }, r.web.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')));
  const roles = ((r.detail || []).find((x) => /open roles/i.test(x.title || '')) || { rows: [] }).rows.length;
  const mail = (r.detail || []).some((x) => x.mail);
  return h('article', { class: 'lead', style: `--c:${pcolor(r.project)}`, tabindex: '0', role: 'button', 'aria-label': `${r.name}: open the detail`,
    onclick: (e) => { if (!e.target.closest('a')) openLead(r); },
    onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) openLead(r); } },
    h('div', { class: 'lead-top' },
      avatar(r.name, r.web),
      h('div', { class: 'lead-id' },
        h('h3', null, r.name),
        h('div', { class: 'where' }, [st.project ? null : pname(r.project), r.segment, r.town].filter(Boolean).join(' · '))),
      h('span', { class: 'st', style: `--sc:${SCOLOR[r.status] || 'var(--dim)'}` }, SWORD[r.status] || r.status)),
    r.chance || r.person || roles || mail ? h('div', { class: 'badges' },
      r.chance ? chanceChip(r.chance) : null,
      mail ? h('span', { class: 'bdg mailready' }, ic('mail'), 'Mail ready') : null,
      roles ? h('span', { class: 'bdg' }, ic('briefcase'), roles === 1 ? '1 open role' : roles + ' open roles') : null,
      r.person ? h('span', { class: 'bdg' }, ic('user'), r.person) : null) : null,
    r.know ? h('p', { class: 'know' }, r.know) : null,
    h('div', { class: 'next' + (r.next ? '' : ' none') }, h('span', null, 'Next'), r.next || (r.status === 'dropped' ? 'Nothing: dropped' : 'Not decided yet')),
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
      return h('section', { class: 'ld-sec' }, head, h('div', { class: 'roles' }, sec.rows.map((x) => h(x.url ? 'a' : 'div', { class: 'role', ...(x.url ? { href: x.url, ...ext(x.url) } : {}) },
        h('span', { class: 'role-t' }, x.text),
        h('span', { class: 'role-m' }, x.fit ? meter(x.fit) : null, x.note || ''),
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
        h('span', { class: 'grp' }, h('i', { class: 'dot', style: `background:${pcolor(r.project)}` }), pname(r.project)),
        r.touched ? h('span', { class: 'when' }, fmtDate(r.touched)) : null)),
    tiles.length ? h('div', { class: 'tiles' }, tiles.map(([icn, k, v, url]) => h('a', { class: 'tile', href: url, ...ext(url) }, ic(icn), h('b', null, k), h('small', null, v)))) : null,
    h('div', { class: 'ld-next' + (r.next ? '' : ' none') }, ic('send'), h('div', null, h('span', null, 'Next'), r.next || (r.status === 'dropped' ? 'Nothing: dropped' : 'Not decided yet'))),
    ...det.filter((x) => x.mail).map((sec) => mailBlock(sec.title, sec.mail)),
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
function mailBlock(title, m) {
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
      copy));
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
      onclick: () => { st.project = st.project === p.id ? '' : p.id; st.status = ''; st.shown = { ...STEP }; render(); } },
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
    projectCards(),
    h('div', { class: 'filters' },
      chips(data.projects.filter((p) => p.leads).map((p) => [p.id, p.leads]), st.project,
        (id) => { st.project = id; st.status = ''; st.shown = { ...STEP }; render(); }, all.length),
      search('Search a name, town, what we know…')),
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
    if (s === painted) return;
    painted = s;
    data = d;
    snap.set('outreach', d);
    try { localStorage.removeItem('outreach.snap'); } catch { /* blocked */ }  // the key before shared.js's snap
    render();
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
