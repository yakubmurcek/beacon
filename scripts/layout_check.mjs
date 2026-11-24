// Every page drawn in a real browser, the way the owner sees it, and measured (run by scripts/layout_check.py).
//
// Static tests can't see a layout: the Decisions badge stretched to a wide pill on Inbox only while it carried a
// short-lived class after a new decision arrived (2026-11-24). So this draws each page at phone and desktop width,
// in both themes and both languages, forces those short-lived states, and fails on what a person would notice:
//   - the shared header is a different size or place on one page than on the others
//   - a pill (badge, count) wider or taller than its text needs
//   - the page wider than the screen (sideways scroll)
//   - text drawn in a system font, which differs per device (the Mac, the iPhone, this box), so wrapping does too
// Usage: node scripts/layout_check.mjs <hub url> [--shots DIR]
import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';

const [, , HUB = 'http://127.0.0.1:4829', flag, SHOTS] = process.argv;
const PAGES = ['/team', '/tasks', '/decisions', '/inbox', '/outreach', '/knowledge', '/usage', '/machines', '/ai',
  '/power', '/buildlog', '/labs', '/rank'];
const VIEWS = [
  { name: 'phone', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 },
  { name: 'desktop', viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 },
];
const MODES = [{ theme: 'paper', lang: 'en' }, { theme: 'cyber', lang: 'cs' }];
const COUNT = '188';  // a three-digit count: the widest the badge gets
// the classes the shell adds at run time (the badge's pulse after a new decision): forced on, as they happen live
const LIVE = [...readFileSync(new URL('../web/hub.js', import.meta.url), 'utf8')
  .matchAll(/badge\.classList\.add\(\s*['"]([\w-]+)['"]/g)].map((m) => m[1]);

function playwright() {
  for (const root of [process.cwd() + '/', execSync('npm root -g').toString().trim() + '/']) {
    for (const name of ['playwright-core', 'playwright']) {
      try { return createRequire(root)(name); } catch { /* next */ }
    }
  }
  throw new Error('playwright-core is not installed: npm ci --ignore-scripts');
}

// Runs in the page: the boxes of the shared header, its pills, sideways scroll.
function measure([count, live]) {
  const badge = document.getElementById('hub-decisions-badge');
  if (badge) {
    badge.textContent = count;
    badge.classList.add(...live);  // the pulse after a new decision, the state that broke on Inbox
    for (const a of badge.getAnimations()) a.finish();
  }
  const box = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const header = {};
  const brand = document.querySelector('.top .brand');
  if (brand) header.brand = box(brand);
  for (const a of document.querySelectorAll('.apps .app')) header['tab ' + a.dataset.app] = box(a).slice(2);  // size only: x moves with the page's own tools
  if (badge) header.badge = box(badge).slice(2);
  const pills = [];
  for (const el of document.querySelectorAll('header .badge, header .count')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !el.textContent.trim()) continue;
    const range = document.createRange(); range.selectNodeContents(el);
    const text = range.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const need = Math.max(text.width + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight), parseFloat(cs.minWidth) || 0);
    if (r.width > need + 3 || r.height > text.height + parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + 3) {
      pills.push(`"${el.textContent.trim()}" is ${Math.round(r.width)}x${Math.round(r.height)}px, its text needs ${Math.round(need)}px wide`);
    }
  }
  return { header, pills, overflow: document.documentElement.scrollWidth - innerWidth };
}

async function systemFonts(page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
  const { root } = await cdp.send('DOM.getDocument', { depth: -1 });
  const { nodeIds } = await cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector: 'body *' });
  const found = new Map();
  for (const id of nodeIds) {
    let fonts;
    try { ({ fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId: id })); } catch { continue; }
    for (const f of fonts) {
      if (f.isCustomFont || /emoji/i.test(f.familyName)) continue;
      const { node } = await cdp.send('DOM.describeNode', { nodeId: id, depth: 1 });
      const cls = (node.attributes || []).find((_, i, a) => a[i - 1] === 'class') || '';
      const text = (node.children || []).filter((c) => c.nodeType === 3).map((c) => c.nodeValue).join('').trim();
      const odd = [...new Set(text)].filter((c) => c.codePointAt(0) > 0x7e).join('');
      // emoji are drawn by each device's emoji font anyway (Apple there, Noto here); no text: generated or blank
      if (!text || (odd && [...odd].every((c) => /\p{Emoji_Presentation}/u.test(c)))) continue;
      const where = node.localName + (cls ? '.' + cls.trim().split(/\s+/).slice(0, 2).join('.') : '')
        + ` (${f.familyName}: "${odd || text.slice(0, 24)}")`;
      found.set(where, (found.get(where) || 0) + f.glyphCount);
    }
  }
  return found;
}

if (!LIVE.length) throw new Error('no run-time badge class found in web/hub.js: update LIVE in this script');
const pw = playwright();
const browser = await pw.chromium.launch({
  executablePath: process.env.CHROME || undefined,
  args: ['--font-render-hinting=none', '--disable-lcd-text', '--hide-scrollbars'],
});
const problems = [];
if (flag === '--shots') mkdirSync(SHOTS, { recursive: true });
for (const view of VIEWS) {
  for (const mode of MODES) {
    const ctx = await browser.newContext({ ...view, locale: mode.lang === 'cs' ? 'cs-CZ' : 'en-US' });
    await ctx.addInitScript(([m, count]) => {
      try {
        localStorage.setItem('hub.theme', m.theme === 'cyber' ? 'cyber' : 'paper');
        localStorage.setItem('hub.lang', m.lang);
        localStorage.setItem('hub.decisionsOpen', count);
      } catch { /* blocked */ }
    }, [mode, COUNT]);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const seen = [];
    for (const path of PAGES) {
      const label = `${path} (${view.name}, ${mode.theme}, ${mode.lang})`;
      await page.goto(HUB + path, { waitUntil: 'networkidle', timeout: 20000 }).catch(() => page.waitForLoadState('load'));
      await page.evaluate(() => document.fonts.ready);
      const m = await page.evaluate(measure, [COUNT, LIVE]);
      seen.push([path, m.header]);
      for (const p of m.pills) problems.push(`${label}: a header pill is stretched: ${p}`);
      if (m.overflow > 1) problems.push(`${label}: the page is ${m.overflow}px wider than the screen`);
      if (view.name === 'desktop' && mode.theme === 'paper') {
        // a second load, as he sees the page all day: fonts cached, nothing held back by font-display: optional
        await page.reload({ waitUntil: 'networkidle', timeout: 20000 }).catch(() => page.waitForLoadState('load'));
        await page.evaluate(() => document.fonts.ready);
        for (const [where, n] of await systemFonts(page)) {
          problems.push(`${label}: ${n} characters in ${where} fall back to a system font, which differs per device; use a web font or another character`);
        }
      }
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${path.slice(1)}-${view.name}-${mode.theme}.png` });
    }
    for (const e of errors) problems.push(`(${view.name}, ${mode.theme}, ${mode.lang}): page error: ${e}`);
    // the shared header: the same boxes on every page
    const [refPath, ref] = seen[0];
    for (const [path, header] of seen.slice(1)) {
      for (const [k, v] of Object.entries(ref)) {
        const w = header[k];
        if (!w) { problems.push(`${path} (${view.name}, ${mode.theme}, ${mode.lang}): the header has no ${k}`); continue; }
        if (v.some((x, i) => Math.abs(x - w[i]) > 1)) {
          problems.push(`${path} (${view.name}, ${mode.theme}, ${mode.lang}): header ${k} is ${w.join(',')}, on ${refPath} ${v.join(',')}`);
        }
      }
    }
    await ctx.close();
  }
}
await browser.close();
if (problems.length) {
  console.log([...new Set(problems)].join('\n'));
  console.log(`\n${problems.length} layout problem(s). Fix the page; the header's own look lives in web/hub.css.`);
  process.exit(1);
}
console.log(`layout ok: ${PAGES.length} pages x ${VIEWS.length} sizes x ${MODES.length} looks`);
