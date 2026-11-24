// Usage page helpers: DOM and SVG building, colours, number and time formats.
import { h, s } from './shared.js';
import { pname } from './names.js';
import { state } from './usage-state.js';

export const svgText = (attrs, text) => s('text', attrs, text);

// Color follows the entity: fixed slots, never assigned by rank.
export const slotColor = (n) => (n ? `var(--s${n})` : 'var(--other)');
export const projectColor = (p) => slotColor(state.colors[p]);

// The recent top projects (from the hub) own a color in every view, so a color always
// means the same project; everything else stays gray rather than borrowing one.
// The period's busiest projects that hold no slot borrow one no project on screen uses,
// so the top of the day is never gray.
export function assignColors(d) {
  const out = { ...state.meta.palette };
  const shown = (d?.by_project || []).filter((p) => !p.name.startsWith('(')).sort((a, b) => b.active_min - a.active_min);
  const taken = new Set(shown.map((p) => out[p.name]).filter(Boolean));
  const free = [1, 2, 3, 4, 5, 6, 7, 8].filter((n) => !taken.has(n));
  for (const p of shown.slice(0, 8)) if (!out[p.name] && free.length) out[p.name] = free.shift();
  return out;
}
// A session's name: its task's title or a short model-written one (the hub sets `title`),
// else the start of its first prompt, else the project.
export function sessName(x) {
  if (x.title) return x.title;
  const q = (x.first_prompt || '').replace(/^\[(scheduled|mid-run)\] /, '').replace(/[`*_#>]/g, '').trim();
  if (!q) return pname(x.project);
  const cut = q.split(/(?<=[.!?])\s|\n/)[0];
  return cut.length > 48 ? cut.slice(0, 46).replace(/\s+\S*$/, '') + '…' : cut;
}
export const swatch = (color) => h('i', { class: 'sw', style: `background:${color}` });

const nf = new Intl.NumberFormat('en-US');
export const fmtInt = (n) => nf.format(Math.round(n || 0));
export function fmtTok(n) {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}
export function fmtUsd(n) {
  if (!n) return '$0';
  if (n < 100) return '$' + n.toFixed(2);
  return '$' + nf.format(Math.round(n));
}
// Axis ticks: whole dollars once they're big enough, so "$75" sits next to "$150", not "$75.00".
export const fmtUsdAxis = (n) => (n >= 10 ? '$' + nf.format(Math.round(n)) : fmtUsd(n));
export function fmtMin(m) {
  m = Math.round(m || 0);
  if (m < 60) return m + 'm';
  return Math.floor(m / 60) + 'h ' + String(m % 60).padStart(2, '0') + 'm';
}
export let TZ = window.BEACON.timezone;  // settings `timezone`, through /config.js; /api/meta confirms it
/** The hub's own timezone, once /api/meta (or the last snapshot of it) says it. */
export function setTZ(tz) { TZ = tz || TZ; }
export const fmtTime = (ts) => new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' }).format(new Date(ts * 1000));
export const fmtDateTime = (ts) => new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { timeZone: TZ, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(ts * 1000));
// The date (in the hub's time zone) a timestamp falls on, as YYYY-MM-DD.
export const localDate = (ts) => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ts * 1000));
export const fmtDur = (sec) => (sec < 60 ? Math.round(sec) + 's' : fmtMin(sec / 60));
export function fmtAgo(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 90) return 'just now';
  if (d < 3600) return Math.round(d / 60) + 'm ago';
  if (d < 86400 * 2) return Math.round(d / 3600) + 'h ago';
  return Math.round(d / 86400) + 'd ago';
}
export function addDays(date, n) {
  const d = new Date(date + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
export const weekday = (date) => (new Date(date + 'T12:00:00Z').getUTCDay() + 6) % 7; // Monday = 0
export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
// Touch screens tap, they don't click.
export const CLICK = matchMedia('(hover: none)').matches ? 'tap' : 'click';
export const cap = (w) => w[0].toUpperCase() + w.slice(1);
export const WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
export function prettyDate(date, year) {
  return new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: year ? 'numeric' : undefined, timeZone: 'UTC' })
    .format(new Date(date + 'T12:00:00Z'));
}
export const shortDate = (date) => new Intl.DateTimeFormat(window.hubLocale || 'en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(date + 'T12:00:00Z'));
export function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  // Fine steps, so a $502 day tops out at $600, not $1,000 (the half line stays round too).
  return [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((m) => m * p).find((x) => x >= v);
}
export const sum = (xs) => xs.reduce((a, b) => a + b, 0);
// Axis tops for minutes, in hours whose half is a whole hour too (so 0 / 12h / 24h, not 12.5h / 25h).
export const niceHours = (m) => ([1, 2, 4, 6, 8, 12, 16, 20, 24, 30, 36, 48, 60, 72, 96].find((x) => x * 60 >= m) ?? Math.ceil(m / 1440) * 24) * 60;
// Lowercase without accents, the same as the hub's search: "ucet" finds "Účet".
export const fold = (s) => (s || '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
// "1 prompt", "2 prompts".
export const count = (n, word) => `${fmtInt(n)} ${word}${n === 1 ? '' : 's'}`;
export const fmtShare = (f) => (f > 0 && f < 0.005 ? '<1%' : Math.round(f * 100) + '%');

// Per-viewer UI preferences; storage can be missing or blocked.
export const prefs = {
  get(k, d) { try { return localStorage.getItem('usage.' + k) || d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('usage.' + k, v); } catch { /* ignore */ } },
};
