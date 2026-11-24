// What the page checks share: the pages, the sizes and looks they are drawn at, and the browser that draws them.
// Used by scripts/layout_check.mjs (the rules) and scripts/ui_states.mjs (the pictures the AI reviewer reads).
import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';

export const PAGES = ['/team', '/tasks', '/decisions', '/inbox', '/outreach', '/knowledge', '/usage', '/machines', '/ai',
  '/power', '/buildlog', '/labs', '/rank'];
export const VIEWS = [
  { name: 'phone', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 },
  { name: 'desktop', viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 },
];
export const MODES = [{ theme: 'paper', lang: 'en' }, { theme: 'cyber', lang: 'cs' }];

function playwright() {
  for (const root of [process.cwd() + '/', execSync('npm root -g').toString().trim() + '/']) {
    for (const name of ['playwright-core', 'playwright']) {
      try { return createRequire(root)(name); } catch { /* next */ }
    }
  }
  throw new Error('playwright-core is not installed: npm ci --ignore-scripts');
}

/** Google Chrome when the box has it ($CHROME), else Playwright's own Chromium; flat text rendering on every box. */
export function launch() {
  return playwright().chromium.launch({
    executablePath: process.env.CHROME || undefined,
    args: ['--font-render-hinting=none', '--disable-lcd-text', '--hide-scrollbars'],
  });
}

/** A browser context in one size and look, with the hub's own settings already stored. */
export async function context(browser, view, mode, extra = {}, options = {}) {
  const ctx = await browser.newContext({ ...view, locale: mode.lang === 'cs' ? 'cs-CZ' : 'en-US', ...options });
  await ctx.addInitScript(([m, store]) => {
    try {
      localStorage.setItem('hub.theme', m.theme === 'cyber' ? 'cyber' : 'paper');
      localStorage.setItem('hub.lang', m.lang);
      for (const [k, v] of Object.entries(store)) localStorage.setItem(k, v);
    } catch { /* blocked */ }
  }, [mode, extra]);
  return ctx;
}

/** Opens a page and waits until it has settled; a page may reload itself once (a new service worker after a deploy). */
export async function open(page, url) {
  for (let tries = 1; ; tries++) {
    try {
      const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 }).catch(() => page.waitForLoadState('load'));
      // the hub restarting (a deploy at night) answers with an error page: try again rather than raise a false alarm
      if (resp && !resp.ok()) throw new Error(`${url} answered ${resp.status()}`);
      await page.evaluate(() => document.fonts.ready);
      return;
    } catch (e) {
      if (tries === 3) throw e;
      await page.waitForTimeout(4000);
    }
  }
}
