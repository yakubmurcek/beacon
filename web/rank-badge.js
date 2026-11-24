// The AI Rank badge, shared by http://beacon/rank (rank.js) and the slim chip on top of Team > Results (#rank-chip).
// Rank is a small flex, not a tab (2026-11-22): the chip is its only way in.
export const escB = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const TIERS = [
  ['IRON', 'Iron', '#7a7a78', 'Nothing yet'],
  ['BRONZE', 'Bronze', '#a8683c', 'Building'],
  ['SILVER', 'Silver', '#8f9aa5', 'Something live'],
  ['GOLD', 'Gold', '#c9971a', '100 users a month'],
  ['PLATINUM', 'Platinum', '#1f9e95', 'In the black after ads'],
  ['EMERALD', 'Emerald', '#1f9d55', '2,000 Kč profit a month'],
  ['DIAMOND', 'Diamond', '#3f74d8', '10,000 Kč profit a month'],
  ['MASTER', 'Master', '#8a4fd0', '~$100 a month passive profit'],
  ['GRANDMASTER', 'Grandmaster', '#d1344b', '~$300 a month passive profit'],
  ['CHALLENGER', 'Challenger', '#e0a100', '~$20,000 a month passive profit'],
];
export const TI = Object.fromEntries(TIERS.map((t, i) => [t[0], i]));
export const color = (tier) => TIERS[TI[tier]][2];
export const nf = new Intl.NumberFormat('en-US');
export const fmtN = (n) => (n >= 10000 ? `${nf.format(Math.round(n / 100) / 10)}k`.replace('.0k', 'k') : nf.format(Math.round(n)));
export const fmtKc = (n) => `${n > 0 && n < 10 ? n.toFixed(1) : nf.format(Math.round(n))} Kč`;
export const fmtV = (s, v) => (s.unit === 'kc' ? fmtKc(v) : fmtN(v));
export const dayLabel = (d) => new Date(d + 'T12:00:00').toLocaleDateString(window.hubLocale || 'en-GB', { day: 'numeric', month: 'short' });

export function emblem(tier, size) {
  const c = color(tier), i = TI[tier];
  // apex tiers (Master, Grandmaster, Challenger) wear a crown with 1-3 gems
  const gems = i >= TI.MASTER ? i - TI.DIAMOND : 0;
  const crown = gems ? [`<path d="M31 21 L33 6 L42 14 L50 3 L58 14 L67 6 L69 21 Z" fill="#f2c94c" stroke="var(--text)" stroke-width="2.5" stroke-linejoin="round"/>`] : [];
  for (let k = 0; k < gems; k++) crown.push(`<circle cx="${50 + (k - (gems - 1) / 2) * 9}" cy="16" r="2.6" fill="${c}" stroke="var(--text)" stroke-width="1.2"/>`);
  return `<svg class="emblem" width="${size}" height="${size}" viewBox="0 0 100 100" aria-hidden="true">
    ${crown.join('')}
    <path d="M50 14 L84 30 V58 C84 76 68 88 50 95 C32 88 16 76 16 58 V30 Z" fill="${c}" stroke="var(--text)" stroke-width="3" stroke-linejoin="round"/>
    <path d="M50 26 L72 36 V57 C72 69 62 77 50 82 C38 77 28 69 28 57 V36 Z" fill="rgba(255,255,255,.28)"/>
    <text x="50" y="64" text-anchor="middle" style="font: 800 26px var(--display); fill: var(--text)">${TIERS[i][1][0]}</text>
  </svg>`;
}

export async function rankChip() {
  const el = document.getElementById('rank-chip');
  if (!el) return;
  try {
    const r = await fetch('/api/rank');
    if (!r.ok) return;
    const n = (await r.json()).now;
    el.style.setProperty('--c', color(n.tier));
    el.innerHTML = `${emblem(n.tier, 34)}<b class="rc-v">${escB(n.label)}</b><span class="rc-lp">${n.lp} LP</span>
      <span class="rc-next">Next: ${escB(n.next)}</span><span class="rc-go">›</span>`;
    el.hidden = false;
  } catch { /* the chip is a nicety: the page works without it */ }
}
