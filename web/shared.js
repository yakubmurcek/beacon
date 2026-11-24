// What the hub's pages share (tasks.js, questions.js): elements, time, markdown, the API, toast
// and menu. Loaded first; each page's script builds on these globals. Plain JS, no build.

export const $ = (id) => document.getElementById(id);

// The last data a page showed, kept in this browser, so the next load paints at once and refreshes
// behind it instead of showing an empty page first. Storage can be full or blocked: then no snapshot.
export const snap = {
  get(k) { try { return JSON.parse(localStorage.getItem('hub.snap.' + k)); } catch { return null; } },
  set(k, v) { snapPut('hub.snap.' + k, JSON.stringify(v)); },
};
// iPhone Safari keeps ~5 MB a site and every page snapshots: when it is full, the biggest other
// snapshots go (that page loads from the hub once) and the page in front keeps its instant paint.
export function snapPut(key, text) {
  for (let i = 0; i < 4; i++) {
    try { localStorage.setItem(key, text); return; } catch { /* full, or blocked */ }
    let big = null, size = 0;
    try {
      for (let j = 0; j < localStorage.length; j++) {
        const k = localStorage.key(j), n = k !== key && k.startsWith('hub.snap.') ? localStorage.getItem(k).length : 0;
        if (n > size) { big = k; size = n; }
      }
    } catch { return; }  // storage blocked: no snapshots at all
    if (!big) return;
    try { localStorage.removeItem(key); localStorage.removeItem(big); } catch { return; }
  }
}
export function setDecisionsBadge(n) {  // the shell's Decisions count (hub.js keeps it fresh between answers)
  const b = $('hub-decisions-badge');
  if (b) b.textContent = n > 0 ? String(n) : '';
  try { localStorage.setItem('hub.decisionsOpen', String(n)); } catch { /* blocked */ }
}
export const DAY = 86400;

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;  // only ever given escaped markdown()
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k.nodeType ? k : String(k));
  return el;
}

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function pragueToday(offset = 0) {
  // Today in the hub's timezone, then whole calendar days on from it (24 h steps would skip a day at a clock change).
  const d = new Date(new Intl.DateTimeFormat('en-CA', { timeZone: window.BEACON.timezone }).format(new Date()) + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);  // YYYY-MM-DD
}

export function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < DAY) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / DAY)} d ago`;
}

// Markdown, the small subset agent runbooks use. Links are cut out of the raw text first and
// every piece is escaped on its own, so nothing in a body (from ClickUp, an agent, a paste) can
// open a tag or leave an attribute; only http(s) URLs become links.
export const LINK = /\[([^\]\n]+)\]\((https?:\/\/[^\s()<>"']+)\)|(https?:\/\/[^\s<>"'()]*[^\s<>"'().,;:!?])/g;

export function styled(text) {  // escaped text -> code, bold, italic
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
}

export function inline(raw) {
  let out = '', last = 0, m;
  LINK.lastIndex = 0;
  while ((m = LINK.exec(raw))) {
    const url = m[2] || m[3];
    out += styled(esc(raw.slice(last, m.index)));
    out += `<a href="${esc(url)}" target="_blank" rel="noopener">${m[1] ? styled(esc(m[1])) : esc(url)}</a>`;
    last = LINK.lastIndex;
  }
  return out + styled(esc(raw.slice(last)));
}

export function markdown(src) {
  // ClickUp escapes markdown punctuation (mouse\_event); show it as written.
  const lines = String(src ?? '').replace(/\\([\\`*_{}\[\]()#+\-.!>|~])/g, '$1').split('\n');
  const out = [];
  let list = null, code = false;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of lines) {
    if (line.startsWith('```')) { close(); out.push(code ? '</pre>' : '<pre>'); code = !code; continue; }
    if (code) { out.push(esc(line) + '\n'); continue; }
    let m;
    if ((m = line.match(/^>\s?(.*)/))) {  // a quoted text he sends as is (a mail): one box, its lines as paragraphs
      if (list !== 'blockquote') { close(); out.push('<blockquote>'); list = 'blockquote'; }
      if (m[1].trim()) out.push(`<p>${inline(m[1])}</p>`);
      continue;
    }
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { close(); out.push(`<h4>${inline(m[2])}</h4>`); continue; }
    if ((m = line.match(/^\s*[-*]\s+(.*)/))) {
      if (list !== 'ul') { close(); out.push('<ul>'); list = 'ul'; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      if (list !== 'ol') { close(); out.push('<ol>'); list = 'ol'; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    close();
    out.push(line.trim() ? `<p>${inline(line)}</p>` : '');
  }
  close();
  if (code) out.push('</pre>');
  return out.join('');
}

// ------------------------------------------------------------------ api

export async function api(path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  // A deploy restarts the hub for a few seconds and the proxy answers 502 meanwhile: reads wait it out quietly.
  let r;
  for (let tries = body === undefined ? 5 : 1; ; ) {
    try {
      r = await fetch(path, opts);
      if (![502, 503, 504].includes(r.status) || --tries <= 0) break;
    } catch {
      if (--tries <= 0) throw new Error('Hub unreachable. Nothing below is live until it answers again.');
    }
    await new Promise((ok) => setTimeout(ok, 1500));
  }
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) throw new Error('Not allowed to write here: open this page through its tailnet name (http://beacon) on your own tailnet login.');
  if (r.status >= 502 && r.status <= 504) throw new Error('Hub is restarting. Back in a moment.');
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

export function showError(e) {
  $('error').textContent = e ? e.message || String(e) : '';
  $('error').hidden = !e;
}

// ------------------------------------------------------------------ toast + menu

export let toastTimer;
export function toast(text, onUndo) {
  const el = $('toast');
  el.replaceChildren(...[h('span', {}, text), onUndo && h('button', { onclick: () => { el.hidden = true; onUndo(); } }, 'Undo')].filter(Boolean));
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 6000);
}

// Copy to the clipboard. Beacon is plain http on the tailnet, where navigator.clipboard does not exist,
// so a hidden textarea and execCommand carry it there (works on iPhone Safari too, inside a tap).
export async function copyText(text) {
  try { if (navigator.clipboard && isSecureContext) { await navigator.clipboard.writeText(text); return true; } } catch { /* fall through */ }
  const ta = h('textarea', { readonly: true, style: 'position:fixed;top:0;left:0;opacity:0' });
  ta.value = text;
  document.body.append(ta);
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { /* old browser */ }
  ta.remove();
  return ok;
}

export function openMenu(anchor, items) {
  const menu = $('menu');
  menu.replaceChildren(...items.map(([label, fn]) => label === '-' ? h('hr') :
    h('button', { onclick: () => { closeMenu(); fn(); } }, label)));
  menu.hidden = false;
  const r = anchor.getBoundingClientRect();
  const w = menu.offsetWidth, hgt = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w))}px`;
  menu.style.top = `${r.bottom + hgt + 8 > window.innerHeight ? Math.max(8, r.top - hgt - 4) : r.bottom + 4}px`;
  setTimeout(() => document.addEventListener('click', closeMenu, { once: true }), 0);
}
export function closeMenu() { $('menu').hidden = true; }
