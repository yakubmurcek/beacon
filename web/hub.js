'use strict';
// The hub shell, on every page: keeps the Decisions badge current. The last count is painted
// by the inline line under the header before first paint, so it never pops in.
(function () {
  const KEY = 'hub.decisionsOpen';
  const SEEN = 'hub.decisionsSeen';  // the newest decision he has had in front of him (questions.js moves it too)
  const badge = document.getElementById('hub-decisions-badge');
  if (!badge) return;
  const onDecisions = location.pathname.startsWith('/decisions');  // that page shows its own "new" pill over the deck
  const show = (n) => { badge.textContent = n > 0 ? String(n) : ''; };
  const get = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
  const put = (k, v) => { try { localStorage.setItem(k, String(v)); } catch { /* storage blocked */ } };
  let pill = null;
  function hide() { if (pill) { pill.remove(); pill = null; } }
  // A decision that came in since he last looked: a pill on top of any page, one tap opens that card (2026-11-24:
  // "when a new decision comes nothing happens on the page").
  function alertNew(qs) {
    const top = Math.max(0, ...qs.map((q) => q.id));
    if (get(SEEN) == null) put(SEEN, top);  // the first visit: what is already open is not news
    const seen = Number(get(SEEN)) || 0;
    const fresh = qs.filter((q) => q.id > seen).sort((a, b) => b.id - a.id);
    if (onDecisions || !fresh.length) return hide();
    const text = fresh.length === 1 ? `New decision: ${fresh[0].question}` : `${fresh.length} new decisions`;
    if (pill && pill.dataset.text === text) return;
    hide();
    pill = document.createElement('div');
    pill.className = 'new-decision'; pill.dataset.text = text; pill.setAttribute('role', 'status');
    const a = document.createElement('a');
    a.href = `/decisions?new=${fresh[0].id}`;
    a.innerHTML = '<span class="dot" aria-hidden="true"></span>';
    a.append(Object.assign(document.createElement('span'), { className: 'txt', textContent: text }));
    a.addEventListener('click', () => put(SEEN, top));
    const x = document.createElement('button');
    x.type = 'button'; x.setAttribute('aria-label', 'Hide'); x.textContent = '×';
    x.addEventListener('click', () => { put(SEEN, top); hide(); });
    pill.append(a, x);
    document.body.append(pill);
    badge.classList.remove('hub-ping'); void badge.offsetWidth; badge.classList.add('hub-ping');
  }
  async function refresh() {
    try {
      const r = await fetch('/api/questions?status=open&limit=500', { cache: 'no-store' });
      if (!r.ok) return;
      const qs = (await r.json()).questions || [];
      show(qs.length);
      put(KEY, qs.length);
      alertNew(qs);
    } catch { /* hub down: keep the last count */ }
  }
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 20000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.hubRefreshBadge = refresh;  // pages call it after he answers
})();

// Night theme (data-theme="cyber" in tokens.css): the head script on each page sets it before first paint;
// this adds the switch at the end of the header's first row, remembers the choice and draws the background
// network. The network moves on the wall clock, so a page switch picks it up where the last page left it.
(function () {
  const KEY = 'hub.theme';
  const root = document.documentElement;
  const META = /** @type {HTMLMetaElement | null} */ (document.querySelector('meta[name="theme-color"]'));
  const plainColor = META ? (META.dataset.plain || META.content) : '';
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  let canvas = null, ctx = null, raf = 0, last = 0, w = 0, h = 0, dpr = 1, nodes = [], colors = [];
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>';
  const row = document.querySelector('.top .top-row');
  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'theme-toggle'; btn.innerHTML = ICON;
  btn.setAttribute('aria-label', 'Night theme');
  // first row: before the line break that starts the page's own views, else at the end of the row
  const brk = row && row.querySelector('.row-break');
  if (brk) row.insertBefore(btn, brk); else if (row) row.appendChild(btn);

  function rgb(hex) {
    const v = hex.replace('#', ''), f = v.length === 3 ? v.split('').map((c) => c + c).join('') : v;
    return [0, 2, 4].map((i) => parseInt(f.slice(i, i + 2), 16)).join(',');
  }
  function seed() {
    // the same layout every load for this screen size: a hash, not Math.random
    const count = Math.max(14, Math.min(46, Math.round(w * h / 26000)));
    nodes = [];
    for (let i = 0; i < count; i++) {
      const r = (k) => { const x = Math.sin((i + 1) * 12.9898 + k * 78.233) * 43758.5453; return x - Math.floor(x); };
      nodes.push({ x: r(1), y: r(2), ax: 0.03 + r(3) * 0.05, ay: 0.03 + r(4) * 0.05,
        sx: 1 / (40 + r(5) * 50), sy: 1 / (40 + r(6) * 50), p: r(7) * 6.283, hue: r(8) });
    }
  }
  function size() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    w = innerWidth; h = innerHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    colors = getComputedStyle(root).getPropertyValue('--net').trim().split(/\s+/).filter(Boolean).map(rgb);
    if (!colors.length) colors = ['255,255,255'];
    seed();
  }
  function draw() {
    const t = Date.now() / 1000;
    ctx.clearRect(0, 0, w, h);
    const pts = nodes.map((d) => ({
      x: (d.x + d.ax * Math.sin(t * d.sx * 6.283 + d.p)) * w,
      y: (d.y + d.ay * Math.cos(t * d.sy * 6.283 + d.p)) * h,
      c: colors[Math.floor(d.hue * colors.length)] }));
    const reach = Math.min(190, Math.max(120, w / 8)), reach2 = reach * reach;
    ctx.lineWidth = 1;
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y, d2 = dx * dx + dy * dy;
        if (d2 > reach2) continue;
        const a = (1 - d2 / reach2) * 0.22;
        ctx.strokeStyle = `rgba(${pts[i].c},${a})`;
        ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke();
        // a data packet on some links, running end to end every few seconds
        if ((i * 7 + j * 3) % 5 === 0) {
          const f = ((t / (2.5 + (i % 4))) + i * 0.17) % 1;
          ctx.fillStyle = `rgba(${pts[j].c},${a * 3.5})`;
          ctx.beginPath(); ctx.arc(pts[i].x + (pts[j].x - pts[i].x) * f, pts[i].y + (pts[j].y - pts[i].y) * f, 1.6, 0, 6.283); ctx.fill();
        }
      }
    }
    for (const p of pts) {
      ctx.fillStyle = `rgba(${p.c},.75)`;
      ctx.shadowColor = `rgba(${p.c},.9)`; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.arc(p.x, p.y, 1.8, 0, 6.283); ctx.fill();
    }
    ctx.shadowBlur = 0;
    // a soft scan line sweeping down every 9 s, in the second network colour
    const sy = ((t % 9) / 9) * (h + 160) - 80, sc = colors[1 % colors.length];
    const g = ctx.createLinearGradient(0, sy - 80, 0, sy + 80);
    g.addColorStop(0, `rgba(${sc},0)`); g.addColorStop(0.5, `rgba(${sc},.05)`); g.addColorStop(1, `rgba(${sc},0)`);
    ctx.fillStyle = g; ctx.fillRect(0, sy - 80, w, 160);
  }
  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (now - last < 33) return;  // ~30 fps is plenty for a background
    last = now; draw();
  }
  function start() {
    if (!canvas) {
      canvas = document.createElement('canvas'); canvas.id = 'cyber-bg'; canvas.setAttribute('aria-hidden', 'true');
      ctx = canvas.getContext('2d');
      document.body.prepend(canvas);
      addEventListener('resize', () => { if (root.dataset.theme === 'cyber') { size(); draw(); } });
    }
    size(); draw();
    requestAnimationFrame(() => canvas.classList.add('on'));
    cancelAnimationFrame(raf);
    if (!reduce.matches && !document.hidden) raf = requestAnimationFrame(loop);
  }
  function stop() { cancelAnimationFrame(raf); raf = 0; if (canvas) canvas.classList.remove('on'); }
  document.addEventListener('visibilitychange', () => {
    if (root.dataset.theme !== 'cyber' || !canvas) return;
    cancelAnimationFrame(raf);
    if (!document.hidden && !reduce.matches) raf = requestAnimationFrame(loop);
  });

  function apply(on) {
    if (on) { root.dataset.theme = 'cyber'; root.style.setProperty('--cy-t', -(Date.now() % 60000) / 1000 + 's'); }
    else delete root.dataset.theme;
    if (META) META.content = on ? getComputedStyle(root).getPropertyValue('--theme-color').trim() || plainColor : plainColor;
    btn.setAttribute('aria-pressed', String(on));
    btn.title = on ? 'Night theme: on' : 'Night theme: off';
    if (on) start(); else stop();
  }
  btn.addEventListener('click', () => {
    const on = root.dataset.theme !== 'cyber';
    try { localStorage.setItem(KEY, on ? 'cyber' : 'plain'); } catch { /* storage blocked: this page only */ }
    const go = () => apply(on);
    if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(go); else go();
  });
  apply(root.dataset.theme === 'cyber');
})();

// Czech or English (i18n.js): a CZ / EN switch after the night theme; the page reloads in the other language.
(function () {
  const theme = document.querySelector('.top .top-row > .theme-toggle');
  if (!theme) return;
  const cs = window.hubLang === 'cs';
  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'theme-toggle lang-toggle'; btn.lang = cs ? 'en' : 'cs';
  btn.textContent = cs ? 'EN' : 'CZ';
  btn.title = cs ? 'Switch to English' : 'Přepnout do češtiny';
  btn.setAttribute('aria-label', btn.title);
  btn.setAttribute('translate', 'no');
  btn.addEventListener('click', () => {
    try { localStorage.setItem('hub.lang', cs ? 'en' : 'cs'); } catch { return; }
    location.reload();
  });
  theme.after(btn);
})();

// The tab icon is the Beacon tower: its windows glow. Browsers show only a still
// frame of an animated SVG icon, so the frames are built from favicon.svg once and cycled.
(function () {
  const link = /** @type {HTMLLinkElement | null} */ (document.querySelector('link[rel="icon"]'));
  if (!link || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const FRAMES = 24, STEP = 100;  // one 2.4 s loop
  fetch(link.href).then((r) => (r.ok ? r.text() : null)).then((svg) => {
    if (!svg || !svg.includes('id="win"')) return;
    const still = svg.replace(/\s*<animate[^>]*\/>/g, '');
    const frames = [];
    for (let i = 0; i < FRAMES; i++) {
      const t = i / FRAMES;
      const glow = (0.35 + 0.65 * (0.5 - 0.5 * Math.cos(2 * Math.PI * t))).toFixed(2);
      const frame = still.replace(/(id="win"[^>]*opacity=")[^"]*/, `$1${glow}`);
      frames.push('data:image/svg+xml,' + encodeURIComponent(frame));
    }
    let i = 0;
    setInterval(() => { link.href = frames[i = (i + 1) % FRAMES]; }, STEP);
  }).catch(() => { /* keep the still icon */ });
})();

// The feedback pen (feedback.js): loaded after the page, so it never slows the first paint.
(function () {
  const s = document.createElement('script');
  s.src = '/feedback.js'; s.defer = true;
  document.head.appendChild(s);
})();
