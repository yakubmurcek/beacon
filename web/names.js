// Project names and initials, the same on every hub page (usage, team). Loaded before the page's script.
import { h } from './shared.js';

// Folder names read badly ("ai-agents-usage-dashboard"): the page shows a project's own name.
// Filters, URLs and CSV keep the folder name.
// The deployment's own names come from /config.js (settings `project_names`), loaded before this file.
const PROJECT_NAMES = (window.BEACON && window.BEACON.project_names) || {};
const ACRONYMS = new Set(['ai', 'ui', 'api', 'cli', 'ios', 'qa', 'llm', 'pdf', 'csv', 'rwby', 'cts']);
export function pname(p) {
  if (!p) return p;
  if (PROJECT_NAMES[p]) return PROJECT_NAMES[p];
  if (/^\d+$/.test(p)) return `Run ${p}`;
  const words = p.replace(/[-_]+/g, ' ').trim().split(/\s+/).map((w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w));
  return words.join(' ').replace(/^./, (c) => c.toUpperCase());
}
export const initials = (p) => { const w = pname(p).replace(/[^\p{L}\p{N} ]/gu, '').split(/\s+/).filter(Boolean); return (w.length > 1 ? w[0][0] + w[1][0] : (w[0] || '?').slice(0, 2)).toUpperCase(); };

// A project is a colored badge with its initials, the same on every task and decision card (2026-11-22:
// "weddings tomorrow started" read as plain text, and "Weddings:" in front of every title took the space). The
// color comes from the name, so a project keeps it on every page and every day.
function projectHue(p) { let x = 7; for (const c of String(p || '')) x = (x * 31 + c.codePointAt(0)) >>> 0; return x % 360; }
export const projChip = (p, extra) => h('span', { class: 'proj-chip', style: `--ph:${projectHue(p)}`, title: pname(p) || 'Personal' },
  h('span', { class: 'proj-av', 'aria-hidden': 'true' }, p && p !== 'personal' ? initials(p) : '★'),
  h('span', { class: 'proj-name' }, (p && p !== 'personal' ? pname(p) : 'Personal') + (extra ? ` ${extra}` : '')));
// A title that starts with its project ("Weddings: the mail …", "Sleep Like CEO 1.1: try …") drops it, the
// badge says it. Returns [title, what followed the name ("1.1"), shown in the badge].
const foldName = (x) => String(x || '').toLowerCase().normalize('NFD').replace(/[^a-z0-9]/g, '');
export function dropProject(title, slug) {
  const m = String(title || '').match(/^([^:?]{2,40}):\s+(\S.*)$/s);
  if (!m || !slug || slug === 'personal') return [title, ''];
  const name = pname(slug), lead = m[1].trim(), rest = m[2].charAt(0).toUpperCase() + m[2].slice(1);
  if ([foldName(slug), foldName(name)].includes(foldName(lead))) return [rest, ''];
  if (!lead.toLowerCase().startsWith(name.toLowerCase() + ' ')) return [title, ''];
  const left = lead.slice(name.length).trim();
  return [rest, rest.includes(left) ? '' : left];
}
export const shortTitle = (t) => dropProject(t.title, t.project)[0];
