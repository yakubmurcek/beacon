'use strict';
// Feedback from any page (task 1913): the floating bubble takes a screenshot of what he sees, he draws on
// it and says what to change, and the hub files it as a task an agent starts on (hub/feedback.py). The page
// keeps a short memory of his last taps and its own errors, so the agent knows how he got there.
// Loaded by hub.js on every hub page (not yet in the passed-through Next.js apps); standalone on purpose.
(function () {
  if (window.hubFeedback) return;
  window.hubFeedback = true;
  const TAPS = 'hub.fb.taps', ERRORS = 'hub.fb.errors', DRAFT = 'hub.fb.draft';
  const LIB = '/vendor/modern-screenshot.min.js';
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M21 12v3a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h8"/><path d="M18.4 2.6a2 2 0 0 1 2.9 2.9L15 11.8l-3.6.9.9-3.6z"/></svg>';
  const svg = (d) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
  const CLOSE = svg('<path d="M18 6 6 18M6 6l12 12"/>');
  const UNDO = svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>');
  const ATTACH = svg('<path d="m21 11-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6L15 6.6"/>');
  const SEND = svg('<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>');

  // ------------------------------------------------------------ what the page remembers
  const now = () => new Date().toTimeString().slice(0, 8);
  const read = (k) => { try { return JSON.parse(sessionStorage.getItem(k) || '[]'); } catch { return []; } };
  const push = (k, item, max) => {
    try { sessionStorage.setItem(k, JSON.stringify(read(k).concat(item).slice(-max))); } catch { /* storage blocked */ }
  };
  const words = (s, max) => (s || '').replace(/\s+/g, ' ').trim().slice(0, max);
  function describe(el) {
    if (!el || el.nodeType !== 1) return '';
    const tag = el.tagName.toLowerCase();
    const cls = [...el.classList].filter((c) => !/^(is-|on$|active$)/.test(c)).slice(0, 3).map((c) => '.' + c).join('');
    return tag + (el.id ? '#' + el.id : '') + cls;
  }
  function label(el) {
    const hit = el.closest('a,button,[role="button"],[role="tab"],input,select,textarea,label,summary,[data-id]') || el;
    const said = hit.getAttribute('aria-label') || hit.getAttribute('title') || hit.value || hit.innerText;
    return describe(hit) + (words(said, 60) ? ' “' + words(said, 60) + '”' : '');
  }
  document.addEventListener('click', (e) => {
    if (e.target.closest && e.target.closest('.fb, .fb-toggle')) return;
    push(TAPS, { at: now(), page: location.pathname + location.hash, what: label(e.target) }, 25);
  }, true);
  const err = (what) => push(ERRORS, { at: now(), page: location.pathname, what: words(what, 300) }, 15);
  addEventListener('error', (e) => err(`${e.message} (${(e.filename || '').split('/').pop()}:${e.lineno})`));
  addEventListener('unhandledrejection', (e) => err('unhandled: ' + ((e.reason && e.reason.message) || e.reason)));
  const consoleError = console.error;
  console.error = function (...a) {
    try { err(a.map((x) => (x && x.message) || String(x)).join(' ')); } catch { /* a value that can't be read: the log goes on */ }
    return consoleError.apply(this, a);
  };
  const realFetch = window.fetch;
  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method = (init && init.method) || 'GET';
    try {
      const r = await realFetch.apply(this, arguments);
      if (!r.ok && !url.includes('/api/feedback')) err(`${method} ${url.split('?')[0]} → ${r.status}`);
      return r;
    } catch (e) {
      if (e.name !== 'AbortError') err(`${method} ${url.split('?')[0]} failed: ${e.message}`);
      throw e;
    }
  };

  // ------------------------------------------------------------ the button
  const btn = document.createElement('button');
  // a round bubble in the bottom corner on every page, like a website's chat button (his call 2026-11-24)
  btn.type = 'button'; btn.className = 'fb-toggle'; btn.innerHTML = ICON;
  btn.title = 'Feedback on this page: draw on it, say what to change';
  btn.setAttribute('aria-label', 'Feedback on this page');
  document.body.appendChild(btn);
  btn.addEventListener('click', open);

  // ------------------------------------------------------------ the screenshot
  let lib = null;
  function loadLib() {
    lib = lib || new Promise((ok, no) => {
      const s = document.createElement('script');
      s.src = LIB; s.onload = () => ok(window.modernScreenshot); s.onerror = () => { lib = null; no(new Error('no screenshot library')); };
      document.head.appendChild(s);
    });
    return lib;
  }
  loadLib().catch(() => {});  // ready before he taps
  const within = (p, ms) => Promise.race([p, new Promise((_, no) => setTimeout(() => no(new Error('timed out')), ms))]);
  async function capture() {
    const ms = await loadLib();
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    const w = innerWidth, h = innerHeight, x = scrollX, y = scrollY;
    // what he sees: the page moved up by the scroll and cut to the window. Moved, the clone has no scroll and its
    // root is the frame, so fixed and sticky parts (the header, the phone's tab bar) are put back where he sees them.
    const pinned = [...document.querySelectorAll('body *')].filter((el) => {
      const p = getComputedStyle(el).position;
      return (p === 'fixed' || p === 'sticky') && !el.closest('.fb-toggle,.fb,.fb-toast');
    }).filter((el, _, all) => !all.some((o) => o !== el && o.contains(el)));  // one inside another moves with it
    pinned.forEach((el) => {
      const r = el.getBoundingClientRect();
      if (getComputedStyle(el).position === 'sticky') {  // stays in the flow: moved by how far it is stuck from its place
        const keep = el.style.position;
        el.style.position = 'static';
        const at = el.getBoundingClientRect().top;
        el.style.position = keep;
        el.dataset.fbPin = 'sticky,' + Math.round(r.top - at);
      } else {
        el.dataset.fbPin = [r.left + x, r.top + y, r.width, r.height].map(Math.round).join(',');
      }
    });
    // text the page draws on one line stays on one line in the copy: the copy's text can come out a hair wider
    // (his Mac at 125 % zoom), and with every box's width copied, chips and labels wrapped and overflowed (2026-11-24)
    const oneLine = oneLineText(h);
    try {
      return await ms.domToCanvas(document.documentElement, {
        // lazy logos below the fold never load, and the library waits 30 s for every image: cap the wait
        timeout: 2500,
        width: w, height: h, scale, backgroundColor: getComputedStyle(document.body).backgroundColor,
        filter: (n) => !(n.classList && ['fb-toggle', 'fb', 'fb-toast'].some((c) => n.classList.contains(c))),
        style: { transform: `translate(${-x}px, ${-y}px)` },
        onCloneNode: (clone) => {
          if (!clone.querySelectorAll) return;
          clone.querySelectorAll('[data-fb-line]').forEach((c) => { c.style.whiteSpace = 'nowrap'; });
          clone.querySelectorAll('[data-fb-pin]').forEach((c) => {
            const [l, t, cw, ch] = c.dataset.fbPin.split(',');
            if (l === 'sticky') { Object.assign(c.style, { position: 'relative', top: t + 'px' }); return; }
            Object.assign(c.style, { position: 'absolute', left: l + 'px', top: t + 'px', width: cw + 'px', height: ch + 'px',
              right: 'auto', bottom: 'auto', margin: '0' });
          });
        },
      });
    } finally {
      pinned.forEach((el) => { delete el.dataset.fbPin; });
      oneLine.forEach((el) => { delete el.dataset.fbLine; });
    }
  }
  function oneLineText(h) {  // marks each element whose text on screen sits on a single line
    const one = new Map(), range = document.createRange();
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT,
      { acceptNode: (t) => (t.data.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT) });
    for (let t; (t = walk.nextNode());) {
      const el = t.parentElement;
      if (!el || el.closest('.fb-toggle,.fb,.fb-toast')) continue;
      range.selectNodeContents(t);
      const rects = [...range.getClientRects()].filter((r) => r.width > 0);
      if (!rects.length || rects[0].bottom < 0 || rects[0].top > h) continue;
      const single = new Set(rects.map((r) => Math.round(r.top))).size === 1;
      one.set(el, (one.has(el) ? one.get(el) : true) && single);
    }
    const marked = [...one].filter(([, single]) => single).map(([el]) => el);
    marked.forEach((el) => { el.dataset.fbLine = ''; });
    return marked;
  }

  // ------------------------------------------------------------ the markup layer
  let ui = null;
  async function open() {
    if (ui || btn.classList.contains('busy')) return;
    btn.classList.add('busy');
    let shot = null, why = '';
    try { shot = await within(capture(), 9000); } catch (e) { why = e.message || 'failed'; err('feedback screenshot: ' + why); }
    btn.classList.remove('busy');
    build(shot);
  }

  function build(shot) {
    const root = document.createElement('div');
    root.className = 'fb';
    root.innerHTML = `<div class="fb-stage"><canvas class="fb-ink"></canvas>
        <p class="fb-hint">${shot ? 'Draw on what should change' : 'No screenshot this time: attach one, or just say it'}</p></div>
      <form class="fb-bar">
        <button type="button" class="fb-icon fb-close" aria-label="Cancel">${CLOSE}</button>
        <button type="button" class="fb-icon fb-undo" aria-label="Undo the last mark" disabled>${UNDO}</button>
        <label class="fb-icon fb-attach" aria-label="Attach a screenshot">${ATTACH}<input type="file" accept="image/*" hidden></label>
        <textarea rows="1" placeholder="What should change?" aria-label="What should change?"></textarea>
        <button type="submit" class="fb-send" aria-label="Send">${SEND}</button>
      </form>
      <p class="fb-error" hidden></p>`;
    document.body.appendChild(root);
    document.documentElement.classList.add('fb-open');
    const stage = root.querySelector('.fb-stage'), ink = root.querySelector('.fb-ink');
    const note = root.querySelector('textarea'), undo = root.querySelector('.fb-undo');
    const hint = root.querySelector('.fb-hint'), error = root.querySelector('.fb-error');
    const send = root.querySelector('.fb-send'), file = root.querySelector('input[type=file]');
    note.value = sessionStorage.getItem(DRAFT) || '';
    const W = innerWidth, H = innerHeight, dpr = Math.min(window.devicePixelRatio || 1, 2);
    let base = shot;  // the picture under the ink: his screen, or one he attached
    let attached = false;  // his own picture: the live page under his marks is not what he marked
    const strokes = [];
    let current = null;
    ink.width = W * dpr; ink.height = H * dpr;
    const ctx = ink.getContext('2d');
    const color = getComputedStyle(document.documentElement).getPropertyValue('--bad').trim() || 'red';

    function fit(img) {  // an attached picture, scaled to fit the screen
      const c = document.createElement('canvas');
      c.width = W * dpr; c.height = H * dpr;
      const k = Math.min(c.width / img.width, c.height / img.height);
      const g = c.getContext('2d');
      g.fillStyle = getComputedStyle(document.body).backgroundColor; g.fillRect(0, 0, c.width, c.height);
      g.drawImage(img, (c.width - img.width * k) / 2, (c.height - img.height * k) / 2, img.width * k, img.height * k);
      return c;
    }
    function paint() {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, ink.width, ink.height);
      if (base) ctx.drawImage(base, 0, 0, ink.width, ink.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.lineCap = ctx.lineJoin = 'round'; ctx.lineWidth = 4; ctx.strokeStyle = color;
      for (const s of strokes) {
        ctx.beginPath();
        s.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        if (s.length === 1) ctx.lineTo(s[0][0] + 0.1, s[0][1]);
        ctx.stroke();
      }
      undo.disabled = !strokes.length;
      hint.hidden = strokes.length > 0;
    }
    paint();

    ink.addEventListener('pointerdown', (e) => {
      ink.setPointerCapture(e.pointerId);
      current = [[e.clientX, e.clientY]]; strokes.push(current); paint();
    });
    ink.addEventListener('pointermove', (e) => {
      if (!current) return;
      for (const p of (e.getCoalescedEvents ? e.getCoalescedEvents() : [e])) current.push([p.clientX, p.clientY]);
      paint();
    });
    const end = () => { current = null; };
    ink.addEventListener('pointerup', end); ink.addEventListener('pointercancel', end);
    undo.addEventListener('click', () => { strokes.pop(); paint(); });
    file.addEventListener('change', () => {
      const f = file.files && file.files[0];
      if (!f) return;
      const img = new Image();
      img.onload = () => { base = fit(img); attached = true; URL.revokeObjectURL(img.src); paint(); };
      img.src = URL.createObjectURL(f);
    });
    const grow = () => { note.style.height = 'auto'; note.style.height = Math.min(note.scrollHeight, 140) + 'px'; };
    note.addEventListener('input', () => { grow(); try { sessionStorage.setItem(DRAFT, note.value); } catch { /* blocked */ } });
    grow();
    // The cursor waits in the box, so he can just type (2026-11-24: "this is basic web standard stuff");
    // a phone keeps its keyboard down until he taps, the screenshot stays free to draw on.
    note.focus({ preventScroll: true });
    note.setSelectionRange(note.value.length, note.value.length);
    const esc = (e) => { if (e.key === 'Escape') close(); };
    const close = () => { removeEventListener('keydown', esc); root.remove(); document.documentElement.classList.remove('fb-open'); ui = null; };
    root.querySelector('.fb-close').addEventListener('click', close);
    addEventListener('keydown', esc);

    // what is under each mark, read from the live page (the overlay lets clicks through for a moment)
    function marks() {
      if (attached) return [];
      root.style.pointerEvents = 'none'; stage.style.pointerEvents = 'none'; ink.style.pointerEvents = 'none';
      const out = [];
      try {
        for (const s of strokes) {
          const xs = s.map((p) => p[0]), ys = s.map((p) => p[1]);
          const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
          const el = document.elementsFromPoint(cx, cy).find((x) => !x.closest('.fb') && x !== document.body && x !== document.documentElement);
          if (!el) continue;
          const box = el.closest('[class*="card"],article,section,li,tr,header,nav') || el;
          const head = box.querySelector && box.querySelector('h1,h2,h3,h4,.title,[class*="title"]');
          out.push({ what: label(el) + (box !== el ? ' in ' + describe(box) : '') + (head && head !== el ? ` (titled “${words(head.innerText, 60)}”)` : ''),
            text: words(el.innerText || el.textContent, 160),
            box: [Math.round(Math.min(...xs)), Math.round(Math.min(...ys)), Math.round(Math.max(...xs)), Math.round(Math.max(...ys))] });
        }
      } finally { root.style.pointerEvents = ''; stage.style.pointerEvents = ''; ink.style.pointerEvents = ''; }
      return out;
    }
    function picture() {
      if (!base && !strokes.length) return null;
      for (const q of [0.82, 0.6, 0.4]) {
        const url = ink.toDataURL('image/jpeg', q);
        if (url.length < 7e6) return url;
      }
      return null;
    }

    root.querySelector('form').addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!note.value.trim() && !strokes.length) { note.focus(); return; }
      send.disabled = true; error.hidden = true;
      const theme = document.documentElement.getAttribute('data-theme') || 'light';
      const body = {
        note: note.value.trim(), picture: picture(), marks: marks(), taps: read(TAPS), errors: read(ERRORS),
        page: { url: location.href, title: words(document.title.replace(/^\(\d+\)\s*/, '').replace(/^Beacon\s*·\s*/, ''), 80), viewport: `${W}×${H}`, theme,
          device: words(navigator.userAgent, 160) },
      };
      try {
        const r = await realFetch('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const got = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(got.error || `the hub answered ${r.status}`);
        try { sessionStorage.removeItem(DRAFT); } catch { /* blocked */ }
        close();
        done(got.task);
      } catch (x) {
        error.textContent = `Not sent: ${x.message}. Your note is kept.`;
        error.hidden = false; send.disabled = false;
      }
    });
    ui = root;
  }

  function done(task) {
    const t = document.createElement('div');
    t.className = 'fb-toast';
    t.innerHTML = `<span>Sent. An agent starts on it in a minute or two.</span> <a href="/tasks#/task/${task}">See it</a>`;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 7000);
  }
})();
