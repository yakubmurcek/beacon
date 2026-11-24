// Inbox: every notice the agents and machines sent (hub/notify.py), whether it reached the phone, and the week's
// pushes against the daily cap. Nothing is lost when a push is held back: it waits here.
import { $, ago, h } from './shared.js';
const st = { filter: 'all', data: null, open: null };
let painted = '';

const STATE = {
  pushed: ['📱', 'Reached the phone'],
  held: ['⏳', 'Waits for the next digest'],
  kept: ['🗂️', 'Inbox only'],
  resolved: ['🟢', 'Fixed before it went out'],
};
const LEVEL = { urgent: ['🔴', 'Urgent'], normal: ['🔔', 'Normal'], inbox: ['🗂️', 'Inbox only'] };
/** @type {[string, string, (notice: any) => boolean][]} */
const FILTERS = [
  ['all', 'All', () => true],
  ['unread', 'Unread', (x) => !x.read_at],
  ['urgent', 'Urgent', (x) => x.level === 'urgent'],
  ['pushed', 'Reached the phone', (x) => x.state === 'pushed'],
  ['held', 'Waiting', (x) => x.state === 'held'],
];
const EMOJI = /^(\p{Extended_Pictographic}|\p{Regional_Indicator}{2})(️|‍\p{Extended_Pictographic})*\s*/u;
const split = (title) => {  // the title's own emoji becomes the card's icon
  const m = title.match(EMOJI);
  return m ? [m[0].trim(), title.slice(m[0].length)] : ['', title];
};
const tr = (s) => (window.hubT ? window.hubT(s) : s);
const dayName = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString(window.hubLocale || 'en-GB', { weekday: 'short' });
const clock = (t) => new Date(t * 1000).toLocaleTimeString(window.hubLocale || 'en-GB', { hour: '2-digit', minute: '2-digit' });

async function load() {
  try {
    const r = await fetch('/api/notify', { cache: 'no-store' });
    if (!r.ok) throw new Error(`The hub answered ${r.status}`);
    const d = await r.json();
    const json = JSON.stringify(d.notices) + JSON.stringify(d.days);
    st.data = d;
    $('error').hidden = true;
    if (json !== painted) { painted = json; render(); }
  } catch (e) {
    $('error').textContent = e.message;
    $('error').hidden = false;
  }
}

async function markRead(body) {
  const r = await fetch('/api/notify/read', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`The hub answered ${r.status}`);
}

function tiles(d) {
  const today = d.days[d.days.length - 1] || { pushes: 0, notices: 0 };
  const held = d.notices.filter((x) => x.state === 'held').length;
  const tile = (icon, k, v, s, cls) => h('div', { class: `tile ${cls || ''}` },
    h('div', { class: 'k' }, h('span', { class: 'ic', 'aria-hidden': 'true' }, icon), tr(k)),
    h('div', { class: 'v' }, v), h('div', { class: 's' }, tr(s)));
  return h('section', { class: 'tiles' },
    tile('✉️', 'Unread', d.unread, 'notices you have not opened', d.unread ? 'hot' : ''),
    tile('📱', 'Pushes today', `${today.pushes}/${d.rules.day_cap}`, `of ${today.notices} notices today`),
    tile('⏳', 'Waiting', held, 'go out in the next digest'),
    tile(d.channel === 'webpush' ? '🔔' : d.channel === 'telegram' ? '✈️' : '⚠️', 'Channel',
      { webpush: 'Beacon app', telegram: 'Telegram', none: 'None' }[d.channel] || d.channel,
      d.channel === 'webpush' ? 'pings from Beacon itself' : 'until pings are on in the Beacon app', d.channel === 'none' ? 'bad' : ''));
}

// ------------------------------------------------------------ this phone's pings (web/sw.js, hub/webpush.py)

const ping = { state: 'unknown', note: '' };
const b64u = (s) => Uint8Array.from(atob((s + '='.repeat((4 - (s.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')),
  (c) => c.charCodeAt(0));

async function pingState() {
  const https = (window.BEACON?.hub?.https || '').replace(/\/$/, '');
  if (location.protocol !== 'https:') ping.state = https ? 'http' : 'nohttps';
  else if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    ping.state = /iPhone|iPad/.test(navigator.userAgent) && !navigator.standalone ? 'install' : 'unsupported';
  } else {
    const reg = await navigator.serviceWorker.getRegistration('/');
    const sub = reg && (await reg.pushManager.getSubscription());
    ping.state = sub && Notification.permission === 'granted' ? 'on' : Notification.permission === 'denied' ? 'denied' : 'off';
  }
  ping.https = https;
}

async function turnOn() {
  ping.note = '';
  try {
    if ((await Notification.requestPermission()) !== 'granted') { ping.state = 'denied'; return render(); }
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    await navigator.serviceWorker.ready;
    const { key } = await (await fetch('/api/notify/vapid')).json();
    if (!key) throw new Error('The hub has no push key yet');
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64u(key) });
    const r = await fetch('/api/notify/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: sub.toJSON(), device: navigator.userAgent.slice(0, 120) }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `The hub answered ${r.status}`);
    ping.state = 'on';
    await test();
  } catch (e) { ping.note = e.message; }
  render();
}

async function test() {
  const r = await fetch('/api/notify/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"urgent": true}' });
  const out = await r.json().catch(() => ({}));
  ping.note = r.ok ? tr('Test sent: it shows on this phone in a few seconds.') : out.error || `The hub answered ${r.status}`;
  render();
}

function pingPanel() {
  const s = ping.state;
  if (s === 'unknown' || s === 'unsupported' || s === 'nohttps') return null;
  const box = (icon, text, ...more) => h('section', { class: `panel ping ${s}` },
    h('div', { class: 'pi', 'aria-hidden': 'true' }, icon), h('div', { class: 'pt' }, h('b', null, tr(text)), ...more,
      ping.note ? h('p', { class: 'muted note' }, ping.note) : null));
  if (s === 'on') return box('✅', 'Pings are on for this phone', ' ', h('button', { type: 'button', class: 'linkish', onclick: test }, tr('Send a test')));
  if (s === 'http') return box('🔒', 'Phone pings need Beacon’s secure address',
    h('p', { class: 'muted' }, tr('Open it there, add it to the Home Screen, and turn pings on from that app.')),
    h('a', { class: 'btn', href: `${ping.https}/inbox` }, tr('Open the secure Beacon'), ' →'));
  if (s === 'install') return box('📲', 'Add Beacon to the Home Screen first',
    h('p', { class: 'muted' }, tr('Share → Add to Home Screen, open Beacon from there, then come back to Inbox.')));
  if (s === 'denied') return box('🔕', 'Notifications are blocked for Beacon',
    h('p', { class: 'muted' }, tr('Settings → Notifications → Beacon → Allow, then reload.')));
  return box('🔔', 'Get pings on this phone',
    h('p', { class: 'muted' }, tr('Urgent ones at once, the rest as a quiet digest. A handful a day at most.')),
    h('button', { type: 'button', class: 'primary', onclick: turnOn }, tr('Turn on')));
}

function week(d) {
  const max = Math.max(1, ...d.days.map((x) => x.notices));
  const bars = d.days.map((x) => h('div', { class: 'day' },
    h('div', { class: 'bars' },
      h('div', { class: `bar notices${x.notices ? '' : ' zero'}`, style: `height:${(x.notices / max) * 100}%` },
        x.notices ? h('b', null, `✉️ ${x.notices}`) : null),
      h('div', { class: `bar pushes${x.pushes ? '' : ' zero'}`, style: `height:${(x.pushes / max) * 100}%` },
        x.pushes ? h('b', null, `📱 ${x.pushes}`) : null)),
    h('div', { class: 'dn' }, dayName(x.day))));
  return h('section', { class: 'panel week' },
    h('div', { class: 'row' }, h('h2', null, tr('This week')),
      h('span', { class: 'muted' }, tr('Notices that came in, and the pushes that reached the phone'))),
    h('div', { class: 'days' }, bars),
    h('ul', { class: 'rules' }, [
      ['🔴', `Urgent: at once, at most ${d.rules.urgent_cap} a day`],
      ['🔔', `Normal: one quiet digest at most every ${d.rules.digest_gap_h} h`],
      ['🌙', `Normal ones never at night, ${d.rules.wake[1]}:00 to ${d.rules.wake[0]}:00`],
      ['🟢', 'Fixed by itself: never pings'],
      ['📵', `At most ${d.rules.day_cap} pushes a day`],
    ].map(([i, t]) => h('li', null, h('span', { 'aria-hidden': 'true' }, i), h('span', null, tr(t))))));
}

function card(x) {
  const [icon, text] = split(x.title);
  const [lvIcon, lvName] = LEVEL[x.level] || ['', x.level];  // only urgent shows its level: normal is the default
  const [sIcon, sName] = STATE[x.state] || ['', x.state];
  const open = st.open === x.id;
  const go = async () => {
    if (!x.read_at) { x.read_at = Date.now() / 1000; markRead({ ids: [x.id] }).catch(() => {}); }
    if (x.link) location.href = x.link;
    else { st.open = open ? null : x.id; render(); }
  };
  return h('article', { class: `notice ${x.level}${x.read_at ? '' : ' unread'}`, tabindex: 0, onclick: go,
    onkeydown: (e) => { if (e.key === 'Enter') go(); } },
    h('div', { class: 'icon', 'aria-hidden': 'true' }, icon || lvIcon),
    h('div', { class: 'main' },
      h('div', { class: 'title' }, text, x.repeats ? h('span', { class: 'rep' }, `×${x.repeats + 1}`) : null),
      x.body ? h('div', { class: `body${open ? ' open' : ''}` }, x.body) : null,
      h('div', { class: 'meta' },
        x.level === 'urgent' ? h('span', { class: 'chip lv urgent' }, h('span', { 'aria-hidden': 'true' }, lvIcon), tr(lvName)) : null,
        h('span', { class: `chip st ${x.state}` }, h('span', { 'aria-hidden': 'true' }, sIcon), tr(sName)),
        x.source ? h('span', { class: 'chip' }, x.source) : null,
        h('span', { class: 'when muted', title: new Date(x.created_at * 1000).toLocaleString() }, ago(x.created_at)),
        x.link ? h('span', { class: 'go' }, tr('Open it'), ' →') : null)));
}

function render() {
  const d = st.data;
  if (!d) return;
  const view = $('view');
  const counts = Object.fromEntries(FILTERS.map(([k, , f]) => [k, d.notices.filter(f).length]));
  const rows = d.notices.filter(FILTERS.find(([k]) => k === st.filter)[2]);
  const chips = h('div', { class: 'filters' },
    FILTERS.map(([k, label]) => h('button', { class: `chip${st.filter === k ? ' on' : ''}`, type: 'button',
      onclick: () => { st.filter = k; render(); } }, tr(label), h('b', null, counts[k]))),
    d.unread ? h('button', { class: 'linkish', type: 'button', onclick: async () => {
      await markRead({ all: true }).catch((e) => { $('error').textContent = e.message; $('error').hidden = false; });
      painted = ''; load();
    } }, '✓ ', tr('Mark all read')) : null);
  const list = rows.length ? h('section', { class: 'list' }, rows.map(card))
    : h('p', { class: 'empty muted' }, st.filter === 'unread' ? tr('All read.') : tr('Nothing here.'));
  const last = d.pushes.find((p) => p.ok);
  const lastLine = last ? h('p', { class: 'last muted' }, '📱 ', tr('Last push'), ` ${clock(last.at)}: ${last.title}`) : null;
  view.replaceChildren(...[pingPanel()].filter(Boolean), tiles(d), week(d), h('section', { class: 'panel' }, chips, lastLine, list));
}

pingState().then(render, () => {});
load();
setInterval(() => { if (!document.hidden) load(); }, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
