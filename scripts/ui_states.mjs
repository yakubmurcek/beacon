// The pictures and page text the AI UI reviewer reads (scripts/ui_review.py runs this against a throwaway hub).
//
// For one data state of the hub (empty, full, ...) it draws every page at phone and desktop size, in the looks asked
// for, and saves per page and look: viewport-sized tiles (a tall full-page shot gets shrunk and clipped text vanishes)
// and what the page says (its visible text, its buttons and links). It also plays the moments that only happen on a
// tap (a decision mid-answer, a task opened) and probes every button for one that does nothing.
// Usage: node scripts/ui_states.mjs <hub url> <out dir> <state> [--looks paper-en,cyber-cs] [--pages /a,/b] [--moments]
//        [--probe]
import { mkdirSync, writeFileSync } from 'node:fs';
import { context, launch, MODES, open, PAGES, VIEWS } from './browser.mjs';

const [, , HUB, OUT, STATE, ...flags] = process.argv;
const arg = (name) => { const i = flags.indexOf(name); return i < 0 ? null : flags[i + 1]; };
const LOOKS = (arg('--looks') || 'paper-en').split(',');
const ONLY = arg('--pages')?.split(',') || PAGES;
const TILES = { phone: 2, desktop: 1 };  // screens per page: the top of a phone page and the next one down
// Taps that open a state the plain page never shows: [page, name, what to tap]
const MOMENTS = [
  ['/decisions', 'mid-answer', '.qcard .opts button'],
  ['/tasks', 'task-open', '.task, [data-task], .row'],
];

// Runs in the page: what a reader would read, and every control with what it says.
function read() {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const controls = [...document.querySelectorAll('button, a[href], [role=button], input, select, textarea')]
    .filter(visible).slice(0, 80).map((el) => {
      const said = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 60);
      return `${el.localName}${el.disabled ? '[disabled]' : ''}: "${said}"${el.getAttribute('aria-label') ? ` aria="${el.getAttribute('aria-label')}"` : ''}`;
    });
  return { title: document.title, text: document.body.innerText.replace(/\n{3,}/g, '\n\n').slice(0, 5000), controls };
}

async function shoot(page, dir, name, view) {
  const files = [];
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  for (let i = 0; i < TILES[view.name] && i * view.viewport.height < height; i++) {
    await page.evaluate((y) => window.scrollTo(0, y), i * view.viewport.height);
    await page.waitForTimeout(150);
    const file = `${name}-${i + 1}.png`;
    await page.screenshot({ path: `${dir}/${file}` });
    files.push(file);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  writeFileSync(`${dir}/${name}.json`, JSON.stringify({ ...(await page.evaluate(read)), shots: files, height }, null, 1));
}

// A button "does something" when within a moment of the tap: a request goes out, the address changes, a dialog or
// tab opens, focus moves, or the page changes more than it did in the same time before the tap (pages poll).
async function probe(page, path) {
  const dead = [];
  await open(page, HUB + path);
  const count = await page.evaluate(() => document.querySelectorAll('button:not([disabled]), [role=button]').length);
  for (let i = 0; i < Math.min(count, 25); i++) {
    await open(page, HUB + path);
    const target = page.locator('button:not([disabled]), [role=button]').nth(i);
    if (!(await target.isVisible().catch(() => false))) continue;
    const label = ((await target.innerText().catch(() => '')) || (await target.getAttribute('aria-label')) || '?').trim().slice(0, 40);
    let requests = 0;
    const onRequest = (r) => { if (r.method() !== 'GET' || !/\/api\/.*(poll|live|stream)/.test(r.url())) requests++; };
    const before = await page.evaluate(() => new Promise((done) => {
      let n = 0; const mo = new MutationObserver((l) => { n += l.length; });
      mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
      setTimeout(() => { mo.disconnect(); done(n); }, 600);
    }));
    await page.evaluate(() => {
      window.__probe = { n: 0, focus: document.activeElement, url: location.href };
      window.__mo = new MutationObserver((l) => { window.__probe.n += l.length; });
      window.__mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    });
    const url = page.url();
    page.on('request', onRequest);
    const popup = page.context().waitForEvent('page', { timeout: 700 }).then(() => true, () => false);
    let dialog = false;
    page.once('dialog', (d) => { dialog = true; d.dismiss().catch(() => {}); });
    try { await target.click({ timeout: 2000 }); } catch { page.off('request', onRequest); continue; }
    await page.waitForTimeout(600);
    page.off('request', onRequest);
    const after = await page.evaluate(() => {
      const p = window.__probe || { n: 0 };
      window.__mo?.disconnect();
      // the tap itself focuses the button: only focus that moved elsewhere counts
      return { n: p.n, moved: document.activeElement !== p.focus && !document.activeElement?.matches('button, [role=button]') };
    }).catch(() => ({ n: 1, moved: true }));  // the page went away: it navigated
    const alive = requests > 0 || page.url() !== url || dialog || (await popup) || after.moved || after.n > before + 2;
    if (!alive) dead.push(label);
  }
  return dead;
}

const browser = await launch();
const dir = `${OUT}/${STATE}`;
mkdirSync(dir, { recursive: true });
const dead = {};
// One page per look and size, as the owner would open it; errors the page throws are kept beside the pictures.
async function withPage(look, view, fn) {
  const [theme, lang] = look.split('-');
  const mode = MODES.find((m) => m.theme === theme && m.lang === lang) || { theme, lang };
  // the test hub's token stands in for the owner's tailnet login, so pages that write (answer, save) work as for him
  const auth = process.env.TASKS_TOKEN ? { extraHTTPHeaders: { Authorization: `Bearer ${process.env.TASKS_TOKEN}` } } : {};
  const ctx = await context(browser, view, mode, {}, auth);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await fn(page);
  if (errors.length) writeFileSync(`${dir}/errors-${view.name}-${look}-${STATE}.json`, JSON.stringify(errors, null, 1));
  await ctx.close();
}

// Three passes, so every picture compared side by side shows the same data: all plain pages first, then the taps
// (a tap may answer a decision), the button probe last (it taps everything).
for (const look of LOOKS) {
  for (const view of VIEWS) {
    await withPage(look, view, async (page) => {
      for (const path of ONLY) {
        await open(page, HUB + path);
        await shoot(page, dir, `${path.slice(1)}-${view.name}-${look}`, view);
      }
    });
  }
}
if (flags.includes('--moments')) {
  for (const look of LOOKS) {
    for (const view of VIEWS) {
      await withPage(look, view, async (page) => {
        for (const [path, name, tap] of MOMENTS.filter(([p]) => ONLY.includes(p))) {
          await open(page, HUB + path);
          const target = page.locator(tap).first();
          if (!(await target.isVisible().catch(() => false))) continue;
          await target.click({ timeout: 3000 }).catch(() => {});
          await page.waitForTimeout(400);
          await shoot(page, dir, `${path.slice(1)}~${name}-${view.name}-${look}`, view);
        }
      });
    }
  }
}
if (flags.includes('--probe')) {
  await withPage(LOOKS[0], VIEWS.find((v) => v.name === 'desktop'), async (page) => {
    for (const path of ONLY) {
      const found = await probe(page, path);
      if (found.length) dead[path] = found;
    }
  });
}
await browser.close();
if (flags.includes('--probe')) writeFileSync(`${dir}/dead-buttons.json`, JSON.stringify(dead, null, 1));
console.log(`${STATE}: ${ONLY.length} pages x ${VIEWS.length} sizes x ${LOOKS.length} looks saved in ${dir}`);
