'use strict';
// Labs: ask Beacon in words or by voice; the answer streams in as cards (hub/labs.py writes the catalog).
// Everything the model wrote goes in through textContent, never as markup.
(function () {
  const $ = (id) => document.getElementById(id);
  const thread = $('thread'), hello = $('hello'), form = $('composer'), input = $('q'), mic = $('mic'),
    heard = $('heard'), viewsEl = $('views'), startersEl = $('starters');
  const params = new URLSearchParams(location.search);
  const HTTPS = window.BEACON.hub.https;  // the hub's HTTPS name (the mic needs a secure page), '' when it has none
  const history = [];  // [{q, title, say}] this visit, sent along so "and last month?" works
  let busy = null;     // the AbortController of the answer streaming now

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }
  const SVG = 'http://www.w3.org/2000/svg';
  function svg(tag, attrs) {
    const e = document.createElementNS(SVG, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  // ------------------------------------------------------------ numbers in words he reads at sight

  function fmt(v, unit, short) {
    if (v == null || v === '') return '—';
    if (typeof v === 'string') return v;
    const abs = Math.abs(v);
    let s;
    if (short && abs >= 10000) s = (v / 1000).toFixed(abs >= 100000 ? 0 : 1).replace(/\.0$/, '') + 'k';
    else if (abs >= 100 || Number.isInteger(v)) s = Math.round(v).toLocaleString('en-US');
    else s = (Math.round(v * 10) / 10).toLocaleString('en-US');
    if (unit === '$') return '$' + s;
    if (unit === 'Kč') return s + ' Kč';
    if (unit === 'h') return s + ' h';
    if (!unit) return s;
    return short ? s : s + ' ' + unit;
  }
  function delta(now, prev, span) {
    if (prev == null || !isFinite(prev) || prev === 0 || typeof now !== 'number') return null;
    const p = Math.round((now - prev) / prev * 100);
    const word = /7/.test(span || '') ? 'last week' : /30/.test(span || '') ? 'the 30 days before' : 'before';
    if (Math.abs(p) < 3) return { text: 'about the same as ' + word, tone: '' };
    return { text: (p > 0 ? '↑ ' : '↓ ') + Math.abs(p) + '% vs ' + word, tone: p > 0 ? 'up' : 'down' };
  }

  // ------------------------------------------------------------ the catalog

  function head(card, c) {
    if (c.title) card.appendChild(el('h3', 'lc-title', c.title));
  }

  const draw = {
    say(c) {
      const card = el('div', 'lc lc-say');
      card.appendChild(el('p', '', c.text));
      return card;
    },
    alert(c) {
      const card = el('div', 'lc lc-alert tone-' + (c.tone || 'warn'));
      card.appendChild(el('span', 'lc-alert-icon', c.tone === 'good' ? '✓' : '!'));
      card.appendChild(el('p', '', c.text));
      return card;
    },
    number(c) {
      const card = el('div', 'lc lc-number');
      head(card, c);
      const d = c.data;
      let value = c.value, unit = c.unit, note = c.note, dl = null;
      if (d && d.kind === 'series') { value = d.total; unit = d.unit; dl = delta(d.total, d.prev, d.span); if (!dl) note = note || d.span; }
      else if (d && d.kind === 'items') { value = d.count; note = note || null; }
      else if (d && d.kind === 'breakdown') { value = d.total != null ? d.total : d.items.reduce((a, x) => a + x.value, 0); unit = d.unit; }
      else if (d && d.kind === 'metric') { value = d.value; note = note || d.next; }
      // money and hours carry their unit; a count reads from its title ("New users 3,786")
      card.appendChild(el('div', 'lc-big', fmt(value, ['$', 'Kč', 'h'].includes(unit) ? unit : '')));
      if (dl) card.appendChild(el('div', 'lc-delta ' + dl.tone, dl.text));
      if (note) card.appendChild(el('div', 'lc-note', note));
      return card;
    },
    bars(c) {
      const card = el('div', 'lc lc-bars');
      head(card, c);
      const d = c.data;
      if (d && d.kind === 'series') return columns(card, d.points, d.unit);
      const items = (d && d.items) || (c.items || []).filter((x) => typeof x === 'object');
      return hbars(card, items, (d && d.unit) || c.unit);
    },
    line(c) {
      const card = el('div', 'lc lc-line');
      head(card, c);
      const d = c.data;
      const pts = (d && d.points) || (c.items || []).filter((x) => typeof x === 'object');
      return line(card, pts, (d && d.unit) || c.unit);
    },
    list(c) {
      const card = el('div', 'lc lc-list');
      head(card, c);
      const d = c.data;
      const items = ((d && d.items) || c.items || []).map((x) => (typeof x === 'string' ? { title: x } : x));
      const lim = c.limit || 6;
      if (!items.length) { card.appendChild(el('p', 'lc-empty', 'Nothing here right now.')); return card; }
      const ul = el('ul', 'lc-rows');
      for (const x of items.slice(0, lim)) {
        const li = el('li', 'tone-' + (x.tone || 'none'));
        li.appendChild(el('span', 'lc-dot'));
        const t = el('div', 'lc-row-t');
        t.appendChild(el('div', 'lc-row-title', x.title || x.label || ''));
        if (x.sub) t.appendChild(el('div', 'lc-row-sub', x.sub));
        li.appendChild(t);
        if (x.value != null) li.appendChild(el('span', 'lc-row-v', fmt(x.value, c.unit)));
        ul.appendChild(li);
      }
      card.appendChild(ul);
      if (items.length > lim) card.appendChild(el('div', 'lc-note', '+ ' + (items.length - lim) + ' more'));
      return card;
    },
    progress(c) {
      const card = el('div', 'lc lc-progress');
      head(card, c);
      const v = Number(c.value) || 0, m = Number(c.max) || 100, pct = Math.max(0, Math.min(100, v / m * 100));
      const big = el('div', 'lc-big', Math.round(pct) + '%');
      card.appendChild(big);
      const bar = el('div', 'lc-track');
      const fill = el('div', 'lc-fill');
      fill.style.width = pct + '%';
      bar.appendChild(fill);
      card.appendChild(bar);
      card.appendChild(el('div', 'lc-note', c.label || (fmt(v, c.unit) + ' of ' + fmt(m, c.unit))));
      return card;
    },
    act(c) {
      // a decision Beacon would answer for him: read back, done only on his yes (here or said aloud)
      const card = el('div', 'lc lc-act');
      card.appendChild(el('p', '', c.text));
      const row = el('div', 'lc-act-row');
      const yes = el('button', 'lc-chip lc-yes', 'Yes, do it');
      const no = el('button', 'lc-chip', 'No');
      yes.type = no.type = 'button';
      yes.addEventListener('click', () => confirmAct(c, card));
      no.addEventListener('click', () => dropAct(card));
      row.appendChild(yes);
      row.appendChild(no);
      card.appendChild(row);
      c.node = card;
      return card;
    },
    suggest(c) {
      const card = el('div', 'lc lc-suggest');
      for (const s of (c.items || []).slice(0, 4)) {
        if (typeof s !== 'string') continue;
        const b = el('button', 'lc-chip', s);
        b.type = 'button';
        b.addEventListener('click', () => ask(s));
        card.appendChild(b);
      }
      return card;
    },
  };

  function columns(card, pts, unit) {
    if (!pts || !pts.length) { card.appendChild(el('p', 'lc-empty', 'No numbers yet.')); return card; }
    const max = Math.max(...pts.map((p) => p.value), 0) || 1;
    const many = pts.length > 10;
    const wrap = el('div', 'lc-cols' + (many ? ' many' : ''));
    pts.forEach((p, i) => {
      const col = el('div', 'lc-col');
      const v = el('span', 'lc-col-v', many && i % Math.ceil(pts.length / 6) && i !== pts.length - 1 ? '' : fmt(p.value, unit, true));
      const bar = el('div', 'lc-col-bar' + (p.value === max ? ' top' : ''));
      bar.style.height = Math.max(2, p.value / max * 100) + '%';
      const lab = el('span', 'lc-col-l', many && i % Math.ceil(pts.length / 6) && i !== pts.length - 1 ? '' : p.label);
      const stack = el('div', 'lc-col-stack');
      stack.appendChild(v);
      stack.appendChild(bar);
      col.appendChild(stack);
      col.appendChild(lab);
      col.title = p.label + ': ' + fmt(p.value, unit);
      wrap.appendChild(col);
    });
    card.appendChild(wrap);
    return card;
  }

  function hbars(card, items, unit) {
    if (!items.length) { card.appendChild(el('p', 'lc-empty', 'Nothing here right now.')); return card; }
    const max = Math.max(...items.map((x) => x.value || 0), 0) || 1;
    const ul = el('div', 'lc-hbars');
    for (const x of items.slice(0, 8)) {
      const row = el('div', 'lc-hbar');
      const top = el('div', 'lc-hbar-top');
      top.appendChild(el('span', 'lc-hbar-l', x.label || x.title || ''));
      top.appendChild(el('span', 'lc-hbar-v', fmt(x.value, unit)));
      const track = el('div', 'lc-track');
      const fill = el('div', 'lc-fill' + (x.label === 'Other' ? ' other' : ''));
      fill.style.width = Math.max(1.5, (x.value || 0) / max * 100) + '%';
      track.appendChild(fill);
      row.appendChild(top);
      row.appendChild(track);
      ul.appendChild(row);
    }
    card.appendChild(ul);
    return card;
  }

  function line(card, pts, unit) {
    if (!pts || pts.length < 2) { card.appendChild(el('p', 'lc-empty', 'Not enough days yet.')); return card; }
    const W = 320, H = 110, P = 6;
    const max = Math.max(...pts.map((p) => p.value), 0) || 1;
    const x = (i) => P + i * (W - 2 * P) / (pts.length - 1);
    const y = (v) => H - P - v / max * (H - 2 * P - 14);
    const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'lc-svg', preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    const d = pts.map((p, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p.value).toFixed(1)).join(' ');
    s.appendChild(svg('path', { d: d + ` L${x(pts.length - 1)} ${H - P} L${x(0)} ${H - P} Z`, class: 'lc-area' }));
    s.appendChild(svg('path', { d, class: 'lc-stroke' }));
    const top = pts.reduce((b, p, i) => (p.value > pts[b].value ? i : b), 0);
    s.appendChild(svg('circle', { cx: x(top), cy: y(pts[top].value), r: 4, class: 'lc-pt' }));
    s.appendChild(svg('circle', { cx: x(pts.length - 1), cy: y(pts[pts.length - 1].value), r: 4, class: 'lc-pt last' }));
    const box = el('div', 'lc-linebox');
    box.appendChild(s);
    // words on the marks: the peak and today, as HTML over the chart so they stay crisp
    const peak = el('span', 'lc-mark', 'peak ' + fmt(pts[top].value, unit, true) + ' · ' + pts[top].label);
    peak.style.left = (x(top) / W * 100) + '%';
    peak.style.top = (y(pts[top].value) / H * 100) + '%';
    if (top > pts.length * 0.7) peak.classList.add('right');
    if (top !== pts.length - 1) box.appendChild(peak);
    const last = el('span', 'lc-mark right end', fmt(pts[pts.length - 1].value, unit, true) + ' · ' + pts[pts.length - 1].label);
    last.style.left = '100%';
    last.style.top = (y(pts[pts.length - 1].value) / H * 100) + '%';
    box.appendChild(last);
    card.appendChild(box);
    const ends = el('div', 'lc-ends');
    ends.appendChild(el('span', '', pts[0].label));
    ends.appendChild(el('span', '', pts[pts.length - 1].label));
    card.appendChild(ends);
    return card;
  }

  function render(c) {
    const f = draw[c.type];
    if (!f) return null;
    const node = f(c);
    if (c.size === 'half') node.classList.add('half');
    if (c.stale) node.classList.add('stale');
    if (c.source && c.data == null && c.type !== 'say') node.classList.add('lc-nodata');
    return node;
  }

  // ------------------------------------------------------------ one answer

  function answerBlock(q) {
    const turn = el('article', 'lab-turn');
    turn.appendChild(el('p', 'lab-q', q));
    const ans = el('div', 'lab-a');
    const bar = el('div', 'lab-a-bar');
    const title = el('span', 'lab-a-title', '');
    const pill = el('span', 'lab-pill thinking', 'Thinking');
    bar.appendChild(title);
    bar.appendChild(pill);
    const grid = el('div', 'lab-grid');
    grid.appendChild(el('div', 'lc lc-ghost'));
    grid.appendChild(el('div', 'lc lc-ghost half'));
    grid.appendChild(el('div', 'lc lc-ghost half'));
    const note = el('p', 'lab-change');
    note.hidden = true;
    ans.appendChild(bar);
    ans.appendChild(note);
    ans.appendChild(grid);
    turn.appendChild(ans);
    thread.appendChild(turn);
    return { turn, title, pill, grid, note, idx: 0, replacing: false, ghosts: true, say: '', key: null, cards: 0 };
  }

  function clearGhosts(a) {
    if (!a.ghosts) return;
    a.grid.querySelectorAll('.lc-ghost').forEach((g) => g.remove());
    a.ghosts = false;
  }

  function setPill(a, text, cls) {
    a.pill.textContent = text;
    a.pill.className = 'lab-pill ' + (cls || '');
  }

  function put(a, card) {
    const node = render(card);
    if (!node) return;
    clearGhosts(a);
    if (card.type === 'say') a.say = card.text;
    if (card.type === 'act') a.act = card;
    if (a.replacing) {
      const old = a.grid.children[a.idx];
      if (old) {
        node.classList.add('fresh');
        a.grid.replaceChild(node, old);
      } else a.grid.appendChild(node);
      a.idx++;
    } else {
      node.classList.add('in');
      a.grid.appendChild(node);
    }
    a.cards++;
  }

  async function ask(q, key) {
    q = String(q || '').trim();
    if (!q) return;
    if (busy) busy.abort();
    busy = new AbortController();
    const mine = busy;
    hello.hidden = true;
    document.body.classList.add('has-thread');
    input.value = '';
    const a = answerBlock(q);
    a.turn.scrollIntoView({ behavior: 'smooth', block: 'start' });
    let res;
    try {
      res = await fetch('/api/labs/ask', {
        method: 'POST', signal: mine.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q, key: key || null, history: history.slice(-4) }),
      });
    } catch (e) {
      if (e.name !== 'AbortError') fail(a, 'Beacon is not answering. Check your connection.');
      return a;
    }
    if (res.status === 401) { fail(a, 'Labs reads your own data, so it opens only on your own login.'); return a; }
    if (!res.ok || !res.body) { fail(a, 'Beacon could not answer (' + res.status + ').'); return a; }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i);
          buf = buf.slice(i + 1);
          if (line.trim()) handle(a, JSON.parse(line));
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') return a;
      if (!a.cards) { fail(a, 'The answer broke off. Try again.'); return a; }
    } finally {
      if (busy === mine) busy = null;
    }
    if (a.pill.classList.contains('thinking') || a.pill.classList.contains('updating')) setPill(a, a.cards ? 'Live' : '', a.cards ? 'live' : '');
    history.push({ q, title: a.title.textContent, say: a.say });
    loadViews();
    return a;
  }

  function handle(a, ev) {
    if (ev.type === 'view') {
      a.title.textContent = ev.title || '';
      if (ev.cached) {
        setPill(a, 'Saved view · live numbers', 'saved');
        a.key = ev.key;
      } else if (ev.refresh) {
        // the model's fresh take on a view already on screen: swap card by card, in place
        a.replacing = true;
        a.idx = 0;
        if (!ev.same) { a.grid.textContent = ''; a.replacing = false; }
        setPill(a, 'Updating', 'updating');
        if (ev.changed) { a.note.textContent = '✨ ' + ev.changed; a.note.hidden = false; }
      } else {
        setPill(a, 'Drawing', 'thinking');
      }
    } else if (ev.type === 'card') {
      // a saved view's own words are from last time: dimmed until the fresh answer replaces them
      if (a.key && !a.replacing && a.pill.classList.contains('saved') && !ev.card.source) ev.card = { ...ev.card, stale: true };
      put(a, ev.card);
    } else if (ev.type === 'cached_done') {
      setPill(a, 'Updating', 'updating');
    } else if (ev.type === 'done') {
      if (a.replacing) while (a.grid.children.length > a.idx) a.grid.lastElementChild.remove();
      setPill(a, 'Live', 'live');
    } else if (ev.type === 'error') {
      if (a.cards) setPill(a, ev.text, 'err');
      else fail(a, ev.text);
    }
  }

  function fail(a, text) {
    clearGhosts(a);
    a.grid.textContent = '';
    a.grid.appendChild(render({ type: 'alert', tone: 'bad', text }));
    setPill(a, '', '');
  }

  // ------------------------------------------------------------ saved views: his own app, grown from questions

  const STARTERS = ['How much did my agents do this week?', 'What needs me right now?', 'Are my machines OK?',
    'How are my users and money doing?'];

  async function loadViews() {
    let views = [];
    try {
      const r = await fetch('/api/labs/views', { cache: 'no-store' });
      if (r.ok) views = (await r.json()).views || [];
    } catch { /* offline: starters only */ }
    viewsEl.textContent = '';
    if (views.length) {
      viewsEl.appendChild(el('h2', 'lab-h2', 'Your views'));
      const grid = el('div', 'lab-view-grid');
      for (const v of views.slice(0, 8)) {
        const b = el('button', 'lab-view');
        b.type = 'button';
        b.appendChild(el('span', 'lab-view-t', v.title));
        b.appendChild(el('span', 'lab-view-q', v.questions[0] || ''));
        b.appendChild(el('span', 'lab-view-n', v.asks > 1 ? 'asked ' + v.asks + '×' : 'asked once'));
        b.addEventListener('click', () => ask(v.questions[0] || v.title, v.key));
        grid.appendChild(b);
      }
      viewsEl.appendChild(grid);
    }
    startersEl.textContent = '';
    const known = new Set(views.flatMap((v) => v.questions.map((q) => q.toLowerCase())));
    const left = STARTERS.filter((s) => !known.has(s.toLowerCase()));
    if (left.length) {
      startersEl.appendChild(el('h2', 'lab-h2', views.length ? 'Or try' : 'Try asking'));
      const row = el('div', 'lab-chips');
      for (const s of left) {
        const b = el('button', 'lc-chip', s);
        b.type = 'button';
        b.addEventListener('click', () => ask(s));
        row.appendChild(b);
      }
      startersEl.appendChild(row);
    }
  }

  // ------------------------------------------------------------ talk: the same on a phone, a desk or a wall screen
  // One talk = turns that flow: Beacon speaks, the mic reopens by itself, 20 s of silence hangs up. Listening and the
  // voice are on the hub (hub/talk.py), so every browser behaves the same. Start: tap the mic (phone), Space
  // (keyboard), or "Hey Beacon" on an always-on screen opened with ?listen=wake.

  const QUIET_HANGUP = 20000, END_OF_TURN = 1100, MAX_TURN = 30000, WAKE_TURN = 4000;
  const FILLER = 'One sec, pulling it together.';
  // an iPad says it is a Mac: a touch screen tells them apart
  const touchMac = /Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1;
  const device = /iPhone|iPad|Android/i.test(navigator.userAgent) || touchMac ? 'phone' : params.has('listen') ? 'wall' : 'desk';
  const ua = navigator.userAgent;
  const browser = (/iPhone|iPad/.test(ua) || touchMac ? 'ios' : /Android/.test(ua) ? 'android' : /Mac/.test(ua) ? 'mac' : 'pc')
    + (/CriOS|Chrome/.test(ua) ? '-chrome' : /Safari/.test(ua) ? '-safari' : '');
  const orb = el('button', 'lab-orb');
  orb.type = 'button';
  orb.hidden = true;
  const orbWord = el('span', 'lab-orb-word', '');
  orb.appendChild(el('span', 'lab-orb-ring'));
  orb.appendChild(orbWord);
  document.body.appendChild(orb);
  const T = { on: false, wake: false, stream: null, ctx: null, an: null, audio: new Audio(), pending: null, stop: null, armed: false, cut: null };
  T.audio.playsInline = true;
  // 0.1 s of real silence (8 kHz, 8 bit): playing it inside the tap lets this audio element speak later on iOS and
  // Safari. An empty clip does not count as playing, and then every later line is refused without a sound.
  const SILENT = (() => {
    const len = 800, b = new Uint8Array(44 + len), v = new DataView(b.buffer);
    const tag = (at, t) => { for (let i = 0; i < 4; i++) b[at + i] = t.charCodeAt(i); };
    tag(0, 'RIFF'); v.setUint32(4, 36 + len, true); tag(8, 'WAVE'); tag(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 8000, true); v.setUint32(28, 8000, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true);
    tag(36, 'data'); v.setUint32(40, len, true); b.fill(128, 44);
    return URL.createObjectURL(new Blob([b], { type: 'audio/wav' }));
  })();
  const tapToHear = el('button', 'lab-orb lab-hear', '');
  tapToHear.type = 'button';
  tapToHear.hidden = true;
  tapToHear.appendChild(el('span', 'lab-orb-ring'));
  tapToHear.appendChild(el('span', 'lab-orb-word', '▶ Tap to hear'));
  document.body.appendChild(tapToHear);

  // During a talk the mic button is the orb (it pulses while listening, goes dark while speaking) and the line
  // above it says what is happening. The big orb only waits for the first tap (?talk, ?listen=wake).
  function state(word, cls) {
    const waiting = cls === 'armed' || cls === 'wake';
    orb.hidden = !waiting;
    orb.className = 'lab-orb ' + (cls || '');
    orbWord.textContent = waiting ? word : '';
    mic.className = 'lab-mic ' + (waiting ? '' : cls || '');
    document.body.classList.toggle('talking', T.on);
    if (T.on) {
      heard.hidden = false;
      heard.textContent = { listening: 'Listening…', thinking: 'Thinking…', speaking: 'Speaking · tap the mic to cut in' }[cls] || word;
    }
  }

  function post(path, body, extra) {
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Talk-Device': device, ...(extra || {}) },
      body: body instanceof Blob ? body : JSON.stringify(body || {}) });
  }

  // The audio context is made inside the tap itself (before any await): iOS keeps one made later silent.
  function audioUnlock() {
    // iPhone: speak through the loudspeaker while the mic is open, and also with the ring switch on silent
    try { if (navigator.audioSession) navigator.audioSession.type = 'play-and-record'; } catch { /* older Safari */ }
    if (!T.ctx) T.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (T.ctx.state !== 'running') T.ctx.resume().catch(() => {});
    const blip = T.ctx.createBufferSource();  // one silent sample inside the tap unlocks the context for good
    blip.buffer = T.ctx.createBuffer(1, 1, 22050);
    blip.connect(T.ctx.destination);
    blip.start(0);
    T.audio.src = SILENT;
    T.audio.play().catch(() => {});
  }

  // Did the speaker play? Sent once per spoken reply, so a silent talk shows why in the hub's log.
  function played(ok, why) { post('/api/talk/played', { ok, why: browser + (why ? ' ' + why : '') }).catch(() => {}); }

  // One clip through the context the tap unlocked; a browser that will not decode it plays it on the element.
  // Resolves 'ended', 'stopped' (he cut in) or throws the reason it could not play.
  async function playClip(blob) {
    const ctx = T.ctx;
    if (ctx) {
      if (ctx.state !== 'running') await Promise.race([ctx.resume().catch(() => {}), new Promise((r) => setTimeout(r, 400))]);
      let buf = null;
      if (ctx.state === 'running') buf = await ctx.decodeAudioData(await blob.arrayBuffer()).catch(() => null);
      if (buf) {
        return new Promise((resolve) => {
          const src = ctx.createBufferSource();
          src.buffer = buf;
          src.connect(ctx.destination);
          let cut = false;
          T.cut = () => { cut = true; try { src.stop(); } catch { /* already done */ } };
          src.onended = () => { T.cut = null; resolve(cut ? 'stopped' : 'ended'); };
          src.start(0);
        });
      }
    }
    return playOnElement(blob, false);
  }

  function playOnElement(blob, tapped) {
    const url = URL.createObjectURL(blob);
    T.audio.src = url;
    return new Promise((resolve, reject) => {
      const done = (how) => { T.cut = null; URL.revokeObjectURL(url); resolve(how); };
      T.cut = () => T.audio.pause();
      T.audio.onended = () => done('ended');
      T.audio.onpause = () => done('stopped');
      T.audio.onerror = () => { T.cut = null; URL.revokeObjectURL(url); reject(new Error('the clip did not play')); };
      T.audio.play().catch((e) => {
        T.cut = null;
        URL.revokeObjectURL(url);
        if (tapped || e.name !== 'NotAllowedError') reject(e); else resolve(askTap(blob));
      });
    });
  }

  // The browser refused sound without a fresh touch: a big button plays the line inside his tap.
  function askTap(blob) {
    played(false, 'refused, asked a tap');
    state('Speaking', 'speaking');
    tapToHear.hidden = false;
    return new Promise((resolve, reject) => {
      tapToHear.onclick = () => {
        tapToHear.hidden = true;
        audioUnlock();
        playOnElement(blob, true).then(resolve, reject);
      };
    });
  }

  async function openMic() {
    if (T.stream) return true;
    if (!window.isSecureContext && location.hostname !== 'localhost' && HTTPS) {
      location.href = HTTPS + '/labs?talk=1';  // the microphone works only over https
      return false;
    }
    try {
      T.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch {
      heard.hidden = false;
      heard.textContent = 'Microphone blocked: allow it for this page.';
      return false;
    }
    if (!T.ctx) T.ctx = new (window.AudioContext || window.webkitAudioContext)();
    T.an = T.ctx.createAnalyser();
    T.an.fftSize = 1024;
    T.ctx.createMediaStreamSource(T.stream).connect(T.an);
    return true;
  }

  function level() {
    const buf = new Float32Array(T.an.fftSize);
    T.an.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    return Math.sqrt(sum / buf.length);
  }

  // One spoken turn: records until he stops talking. null when nobody spoke within `quiet` ms.
  function record(quiet, longest) {
    return new Promise((resolve) => {
      const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || '';
      const rec = new MediaRecorder(T.stream, type ? { mimeType: type } : undefined);
      const chunks = [];
      const t0 = Date.now();
      let floor = 0.01, spoke = 0, lastVoice = 0, done = false;
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = () => resolve(spoke ? { blob: new Blob(chunks, { type: rec.mimeType || type }), secs: (Date.now() - t0) / 1000 } : null);
      const finish = () => { if (done) return; done = true; clearInterval(tick); T.stop = null; if (rec.state !== 'inactive') rec.stop(); };
      T.stop = () => { spoke = 0; finish(); };
      const tick = setInterval(() => {
        const v = level(), now = Date.now();
        if (!spoke) floor = floor * 0.95 + Math.min(v, 0.05) * 0.05;
        const loud = v > Math.max(0.015, floor * 3);
        if (loud) { if (!spoke) spoke = now; lastVoice = now; }
        if (spoke && now - lastVoice > END_OF_TURN && lastVoice - spoke > 250) finish();
        else if (!spoke && now - t0 > quiet) finish();
        else if (now - t0 > longest) finish();
      }, 50);
      rec.start(250);
    });
  }

  async function hear(turn, kind) {
    const r = await post('/api/talk/hear', turn.blob, { 'Content-Type': turn.blob.type || 'audio/webm', 'X-Talk-Seconds': turn.secs.toFixed(1), 'X-Talk-Kind': kind || 'hear' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || 'Beacon could not hear that.');
    return String(j.text || '').trim();
  }

  // Long text is spoken a sentence at a time, the next one fetched while one plays, so a self-hosted voice that
  // renders each sentence in a few seconds starts talking at once instead of after the whole brief.
  function pieces(text) {
    const out = [];
    for (const s of String(text).match(/[^.!?]+[.!?]+["')\]]*\s*|[^.!?]+$/g) || []) {
      const last = out.length - 1;
      if (last >= 0 && out[last].length + s.length < 90) out[last] += s; else out.push(s);
    }
    return out.map((x) => x.trim()).filter(Boolean);
  }

  async function say(text) {
    if (!text) return;
    state('Speaking', 'speaking');
    const parts = pieces(text);
    const got = [];
    const fetchPart = (i) => got[i] || (got[i] = post('/api/talk/say', { text: parts[i] }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'no voice');
      return r.blob();
    }));
    let sounded = false;
    try {
      for (let i = 0; i < parts.length; i++) {
        const blob = await fetchPart(i);
        if (i + 1 < parts.length) fetchPart(i + 1).catch(() => {});
        const how = await playClip(blob);
        if (!sounded) { sounded = true; played(true); }
        if (how !== 'ended') break;  // he cut in: the rest stays unsaid
      }
    } catch (e) {
      if (!sounded) played(false, String(e.name || e.message || e).slice(0, 30));
      heard.hidden = false;
      heard.textContent = 'No sound: ' + String(e.message || e);
    }
  }

  async function brief() {
    const filler = say(FILLER);  // speaks at once while the brief is gathered
    hello.hidden = true;
    document.body.classList.add('has-thread');
    const a = answerBlock('Brief me');
    a.turn.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const r = await post('/api/talk/brief', {});
    const b = await r.json().catch(() => ({}));
    if (!r.ok) { fail(a, b.error || 'No brief right now.'); await filler; return; }
    a.title.textContent = 'Brief';
    for (const c of b.cards || []) put(a, c);
    setPill(a, 'Live', 'live');
    history.push({ q: 'brief me', title: 'Brief', say: b.say });
    await filler;
    await say(b.say);
  }

  async function confirmAct(c, card) {
    T.pending = null;
    card.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    const r = await post('/api/talk/act', { do: 'answer', decision: c.decision, answer: c.answer });
    const j = await r.json().catch(() => ({}));
    const ok = r.ok;
    card.classList.add(ok ? 'done' : 'failed');
    card.appendChild(el('p', 'lc-note', ok ? '✓ Answered: ' + c.answer : (j.error || 'Could not answer it.')));
    return ok;
  }
  function dropAct(card) {
    T.pending = null;
    card.classList.add('dropped');
    card.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  }

  const YES = /\b(yes|yeah|yep|yup|sure|do it|go ahead|confirm|correct|right)\b/i;
  const NO = /\b(no|nope|cancel|don'?t|wait|stop)\b/i;
  const BYE = /^(bye|goodbye|stop|that'?s (all|it)|thanks?( you)?|nothing|i'?m good|done)\b/i;

  // what he said, in a talk: returns false when the talk should end
  async function turn(text) {
    heard.hidden = false;
    heard.textContent = text;
    if (T.pending) {
      const p = T.pending;
      if (YES.test(text) && !NO.test(text)) { const ok = await confirmAct(p, p.node); await say(ok ? 'Done.' : 'That did not go through.'); return true; }
      if (NO.test(text)) { dropAct(p.node); await say('Okay, left as it is.'); return true; }
      dropAct(p.node);
    }
    if (BYE.test(text.trim())) { await say('Okay.'); return false; }
    if (/\bbrief\b|what'?s (up|new|going on)|catch me up/i.test(text)) { await brief(); return true; }
    state('Thinking', 'thinking');
    const a = await ask(text);
    if (!a) return true;
    let words = a.say || '';
    if (a.act && a.act.node) { T.pending = a.act; words += ' ' + a.act.text + ' Shall I?'; }
    await say(words.trim());
    return true;
  }

  async function startTalk(first) {
    if (T.on || T.starting) return;  // a tap and a key at once start one talk
    T.starting = true;
    audioUnlock();
    const ok = await openMic();
    T.starting = false;
    if (!ok) return;
    if (T.ctx.state === 'suspended') T.ctx.resume();
    T.on = true;
    post('/api/talk/warm', {}).catch(() => {});  // the hub sends it on at most once a minute
    post('/api/talk/start', {}).then((r) => (r.status === 429 ? r.json() : null)).then((j) => { if (j) { heard.hidden = false; heard.textContent = j.error; endTalk(); } }).catch(() => {});
    let text = first || '';
    while (T.on) {
      if (!text) {
        state('Listening', 'listening');
        const t = await record(QUIET_HANGUP, MAX_TURN);
        if (!T.on) break;
        if (!t) break;  // 20 s of silence: hang up
        state('Thinking', 'thinking');
        try { text = await hear(t); } catch (e) { heard.hidden = false; heard.textContent = e.message; if (/minutes/.test(e.message)) break; text = ''; continue; }
        if (!text) continue;
      }
      const go = await turn(text);
      text = '';
      if (!go) break;
    }
    endTalk();
  }

  function endTalk() {
    T.on = false;
    T.pending = null;
    if (T.stop) T.stop();
    if (T.cut) T.cut();
    tapToHear.hidden = true;
    state('', '');
    heard.hidden = true;
    if (T.wake) { state('Say “Hey Beacon”', 'wake'); wakeLoop(); }
  }

  // An always-on screen: short turns checked for "Beacon"; what follows the name is the first thing he asked.
  async function wakeLoop() {
    if (T.waking) return;
    T.waking = true;
    while (T.wake && !T.on) {
      const t = await record(60000, WAKE_TURN);
      if (!t || T.on) continue;
      let text = '';
      try { text = await hear(t, 'wake'); } catch (e) { heard.hidden = false; heard.textContent = e.message; await new Promise((r) => setTimeout(r, 60000)); continue; }
      const m = text.match(/\bbeacon\b[\s,.!?]*(.*)$/i);
      if (m) { T.waking = false; startTalk(m[1].trim()); return; }
    }
    T.waking = false;
  }

  // ?talk (a phone Shortcut, Back Tap) and ?listen=wake (a wall screen): a browser starts sound and the mic only
  // after a touch or a key, so the page waits for the first one anywhere.
  function armTalk(wake) {
    T.wake = wake;
    state(wake ? 'Tap once to start listening' : 'Tap to talk', 'armed');
    const go = async (e) => {
      if (e.type === 'keydown' && e.target.closest('input, textarea')) return;
      e.stopImmediatePropagation();
      if (e.type === 'keydown') e.preventDefault();
      removeEventListener('pointerdown', go, true);
      removeEventListener('keydown', go, true);
      if (!wake) return startTalk();
      audioUnlock();
      if (await openMic()) { state('Say “Hey Beacon”', 'wake'); wakeLoop(); }
    };
    addEventListener('pointerdown', go, true);
    addEventListener('keydown', go, true);
  }

  mic.addEventListener('click', () => {
    if (T.on && mic.classList.contains('speaking')) { if (T.cut) T.cut(); }  // cut in: it listens again
    else if (T.on) endTalk();
    else startTalk();
  });
  addEventListener('keydown', (e) => {
    if (e.code !== 'Space' || e.repeat || e.target.closest('input, textarea, button, a')) return;
    e.preventDefault();
    if (T.on) endTalk(); else startTalk();
  });

  form.addEventListener('submit', (e) => { e.preventDefault(); ask(input.value); });

  // ?q= opens with a question (a link from anywhere, or a shortcut on the phone)
  const first = params.get('q');
  loadViews();
  if (first) ask(first);
  // Opened by the short http name: hop to the secure one now, so the one tap there starts the talk.
  const toTalk = params.has('talk') || params.get('listen') === 'wake';
  if (toTalk && !window.isSecureContext && location.hostname !== 'localhost' && HTTPS) location.replace(HTTPS + '/labs' + location.search);
  else if (toTalk) {
    armTalk(params.get('listen') === 'wake');
    if (params.has('talk')) post('/api/talk/warm', {}).catch(() => {});  // the voice loads while he taps
  }
})();
