// The in-app viewer for what a decision shows (2026-11-22: "zoom the image … so it doesn't route me to a new
// tab … F key to full screen it and back"). A tap on a card's picture, or on a link to a video, opens it here:
// pinch, wheel or double-tap zooms, drag pans, swipe or ← → moves between them, F is full screen, Esc or Back closes.
// The links keep their href, so a long press or middle click still opens a tab. Imported for its effect by every page with cards (tasks.js, questions.js).
const VIDEO = /\.(mp4|webm|mov|m4v)(\?.*)?$/i;
const IMAGE = /\.(png|jpe?g|webp|gif|avif)(\?.*)?$/i;

// A link the viewer can show itself: a picture, a video file, or a YouTube / Vimeo / Loom player.
function mediaOf(href) {
  let u;
  try { u = new URL(href, location.href); } catch { return null; }
  if (VIDEO.test(u.pathname)) return { kind: 'video', src: u.href };
  if (IMAGE.test(u.pathname) || u.pathname.startsWith('/api/questions/file/')) return { kind: 'image', src: u.href };
  const host = u.hostname.replace(/^(www|m)\./, '');
  let id;
  if (host === 'youtu.be') id = u.pathname.slice(1);
  else if (host === 'youtube.com') id = u.searchParams.get('v') || (u.pathname.match(/^\/(shorts|embed|live)\/([\w-]+)/) || [])[2];
  if (id) return { kind: 'embed', src: `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&playsinline=1` };
  if (host === 'vimeo.com' && /^\/\d+/.test(u.pathname)) return { kind: 'embed', src: `https://player.vimeo.com/video/${u.pathname.split('/')[1]}?autoplay=1` };
  if (host === 'loom.com' && (id = (u.pathname.match(/^\/(share|embed)\/(\w+)/) || [])[2])) return { kind: 'embed', src: `https://www.loom.com/embed/${id}?autoplay=1` };
  return null;
}
const MEDIA_LINK = 'a.see-pic, a.see-link, a.step-go';
function itemOf(a) {
  const m = mediaOf(a.getAttribute('href') || '');
  if (!m) return null;
  const label = a.querySelector('.see-cap, .see-go, .step-text')?.textContent.replace(/^👀\s*/, '') || a.querySelector('img')?.alt || '';
  return { ...m, href: a.href, label };
}
// Everything on the same card is one gallery, in the order he sees it.
function galleryFor(a) {
  const scope = a.closest('.qcard') || a.parentElement;
  const links = [...scope.querySelectorAll(MEDIA_LINK)].filter((x) => itemOf(x));
  return { items: links.map(itemOf), links, at: Math.max(0, links.indexOf(a)) };
}

let box = null;           // the overlay while it is open
let items = [], links = [], at = 0;
let z = { s: 1, x: 0, y: 0 };  // zoom and pan of the current picture
const el = (tag, cls, attrs = {}) => Object.assign(document.createElement(tag), cls ? { className: cls } : {}, attrs);

function build() {
  box = el('div', 'viewer', { role: 'dialog', ariaModal: 'true', ariaLabel: 'Picture viewer', tabIndex: -1 });
  box.innerHTML = '<div class="viewer-stage"></div>'
    + '<div class="viewer-bar"><span class="viewer-count"></span><span class="viewer-label"></span>'
    + '<a class="viewer-btn viewer-tab" target="_blank" rel="noopener" title="Open in a new tab" aria-label="Open in a new tab">↗</a>'
    + '<button class="viewer-btn viewer-full" title="Full screen (F)" aria-label="Full screen"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>'
    + '<button class="viewer-btn viewer-close" title="Close (Esc)" aria-label="Close">✕</button></div>'
    + '<button class="viewer-nav prev" aria-label="Previous">‹</button><button class="viewer-nav next" aria-label="Next">›</button>'
    + `<div class="viewer-hint">${matchMedia('(hover: none)').matches ? 'Pinch or double-tap to zoom · swipe down to close' : 'Scroll or double-click to zoom · F full screen · Esc closes'}</div>`;
  box.querySelector('.viewer-close').onclick = () => close();
  box.querySelector('.viewer-full').onclick = () => toggleFull();
  box.querySelector('.prev').onclick = () => go(at - 1);
  box.querySelector('.next').onclick = () => go(at + 1);
  gestures(box.querySelector('.viewer-stage'));
  // Nothing inside reaches the card under it (its swipe answers the decision).
  ['pointerdown', 'pointermove', 'pointerup', 'touchstart', 'touchmove', 'click', 'wheel'].forEach((t) =>
    box.addEventListener(t, (e) => e.stopPropagation(), { passive: t !== 'wheel' }));
  document.body.append(box);
}

function open(g) {
  if (!g.items.length) return;
  ({ items, links } = g);
  const i = g.at;
  if (!box) build();
  if (!box.isConnected) document.body.append(box);
  box.classList.add('on');
  document.documentElement.classList.add('viewer-open');
  if (!history.state?.viewer) history.pushState({ ...(history.state || {}), viewer: 1 }, '');  // Back closes it
  show(i);
  box.focus({ preventScroll: true });
}
function close(fromBack) {
  if (!box?.classList.contains('on')) return;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  box.classList.remove('on');
  box.querySelector('.viewer-stage').replaceChildren();  // stops a playing video
  document.documentElement.classList.remove('viewer-open');
  if (!fromBack && history.state?.viewer) history.back();
  // The card's slideshow lands on the picture he was last looking at.
  const a = links[at], track = a?.closest('.see-track');
  if (track) track.scrollTo({ left: [...track.querySelectorAll('.see-pic')].indexOf(a) * track.clientWidth });
}
function go(i) { if (i >= 0 && i < items.length && i !== at) show(i); }
function show(i) {
  at = i;
  const it = items[i], stage = box.querySelector('.viewer-stage');
  z = { s: 1, x: 0, y: 0 };
  let media;
  if (it.kind === 'image') media = el('img', 'viewer-media', { src: it.src, alt: it.label || 'Picture', draggable: false });
  else if (it.kind === 'video') media = el('video', 'viewer-media', { src: it.src, controls: true, autoplay: true, playsInline: true });
  else media = el('iframe', 'viewer-media viewer-embed', { src: it.src, allow: 'autoplay; fullscreen; picture-in-picture', allowFullscreen: true });
  stage.replaceChildren(media);
  stage.dataset.kind = it.kind;
  box.querySelector('.viewer-count').textContent = items.length > 1 ? `${i + 1} / ${items.length}` : '';
  box.querySelector('.viewer-label').textContent = it.label || '';
  box.querySelector('.viewer-tab').href = it.href;
  box.querySelector('.prev').hidden = i === 0;
  box.querySelector('.next').hidden = i === items.length - 1;
  paint();
}
function paint(smooth) {
  const m = box.querySelector('.viewer-stage img');
  if (!m) return;
  m.style.transition = smooth ? 'transform .18s ease' : '';
  m.style.transform = `translate(${z.x}px, ${z.y}px) scale(${z.s})`;
  box.classList.toggle('zoomed', z.s > 1.01);
}
// Zoom to s around a point on screen (cx, cy), keeping that point under the finger or cursor.
function zoomAt(s, cx, cy, smooth) {
  const stage = box.querySelector('.viewer-stage').getBoundingClientRect();
  s = Math.min(8, Math.max(1, s));
  const ox = cx - (stage.left + stage.width / 2), oy = cy - (stage.top + stage.height / 2);
  z.x = ox - (ox - z.x) * (s / z.s);
  z.y = oy - (oy - z.y) * (s / z.s);
  z.s = s;
  if (s === 1) z.x = z.y = 0;
  paint(smooth);
}
function toggleFull() {
  if (document.fullscreenElement) return document.exitFullscreen().catch(() => {});
  const v = box.querySelector('.viewer-stage video');
  if (box.requestFullscreen) return box.requestFullscreen().catch(() => {});
  if (v?.webkitEnterFullscreen) v.webkitEnterFullscreen();  // iPhone: only a video can go full screen
}

function gestures(stage) {
  const pts = new Map();
  let start = null, lastTap = 0;
  stage.addEventListener('pointerdown', (e) => {
    if (stage.dataset.kind !== 'image') return;
    try { stage.setPointerCapture(e.pointerId); } catch { /* a pointer the browser no longer tracks */ }
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const p = [...pts.values()];
    start = { z: { ...z }, p: p.map((q) => ({ ...q })), d: p.length > 1 ? Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) : 0, moved: false };
  });
  stage.addEventListener('pointermove', (e) => {
    if (!pts.has(e.pointerId) || !start) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const p = [...pts.values()];
    if (p.length > 1 && start.d) {  // pinch
      const mx = (p[0].x + p[1].x) / 2, my = (p[0].y + p[1].y) / 2;
      z = { ...start.z };
      zoomAt(start.z.s * Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) / start.d, mx, my);
      start.moved = true;
      return;
    }
    const dx = p[0].x - start.p[0].x, dy = p[0].y - start.p[0].y;
    if (Math.abs(dx) + Math.abs(dy) > 6) start.moved = true;
    if (z.s > 1.01) { z.x = start.z.x + dx; z.y = start.z.y + dy; paint(); }  // pan
    else {  // not zoomed: the picture follows the finger, to swipe on or down to close
      const m = stage.querySelector('img');
      m.style.transform = Math.abs(dy) > Math.abs(dx) ? `translateY(${Math.max(0, dy)}px)` : `translateX(${dx}px)`;
      box.style.setProperty('--fade', Math.abs(dy) > Math.abs(dx) ? Math.max(0.3, 1 - Math.max(0, dy) / 400) : 1);
    }
  });
  const end = (e) => {
    if (!pts.has(e.pointerId)) return;
    const p0 = start?.p[0], last = pts.get(e.pointerId);
    pts.delete(e.pointerId);
    if (pts.size) {  // one finger of a pinch lifted: the other carries on as a pan
      const p = [...pts.values()];
      start = { z: { ...z }, p: p.map((q) => ({ ...q })), d: 0, moved: true };
      return;
    }
    const s = start; start = null;
    box.style.setProperty('--fade', 1);
    if (!s) return;
    if (!s.moved) {  // a tap: two quick ones zoom in or back out
      const now = Date.now();
      if (now - lastTap < 300) { zoomAt(z.s > 1.01 ? 1 : 2.5, last.x, last.y, true); lastTap = 0; }
      else lastTap = now;
      return;
    }
    if (s.d || s.z.s > 1.01) return;
    const dx = last.x - p0.x, dy = last.y - p0.y;
    if (dy > 110 && dy > Math.abs(dx)) return close();
    if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy)) {
      const to = at + (dx < 0 ? 1 : -1);
      if (to >= 0 && to < items.length) return show(to);
    }
    paint(true);  // not far enough: spring back
  };
  stage.addEventListener('pointerup', end);
  stage.addEventListener('pointercancel', end);
  stage.addEventListener('wheel', (e) => {
    if (stage.dataset.kind !== 'image') return;
    e.preventDefault();
    zoomAt(z.s * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.002)), e.clientX, e.clientY);
  }, { passive: false });
  // A click on the dark space around the picture closes, like every phone gallery.
  stage.addEventListener('click', (e) => { if (e.target === stage && z.s <= 1.01) close(); });
}

// The picture he is looking at on the current card: the visible slide of its slideshow.
function visibleOnCard() {
  const card = document.querySelector('.deck-card') || document.querySelector('.qcard.focus') || document.querySelector('.qcard');
  const a0 = card?.querySelector('a.see-pic') || [...(card?.querySelectorAll(MEDIA_LINK) || [])].find(itemOf);
  if (!a0) return null;
  const track = a0.closest('.see-track');
  if (!track) return a0;
  const i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
  return track.querySelectorAll('.see-pic')[i] || a0;
}

document.addEventListener('click', (e) => {
  const a = /** @type {Element} */ (e.target).closest?.(MEDIA_LINK);
  if (!a || e.defaultPrevented || e.button > 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || !itemOf(a)) return;
  e.preventDefault();
  open(galleryFor(a));
});
// Capture phase, before the deck's own keys: an arrow in here must never answer the decision under it.
window.addEventListener('keydown', (e) => {
  const typing = /** @type {Element} */ (e.target).matches?.('input, textarea, select, [contenteditable]');
  if (!box?.classList.contains('on')) {
    if (typing || e.metaKey || e.ctrlKey || e.altKey || e.key.toLowerCase() !== 'f') return;
    const a = visibleOnCard();
    if (!a) return;
    e.preventDefault(); e.stopImmediatePropagation();
    open(galleryFor(a));
    return toggleFull();
  }
  if (e.metaKey || e.ctrlKey) return;
  e.stopImmediatePropagation();
  const k = e.key;
  const r = box.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  if (k === 'Escape') { e.preventDefault(); close(); }
  else if (k === 'ArrowRight') { e.preventDefault(); go(at + 1); }
  else if (k === 'ArrowLeft') { e.preventDefault(); go(at - 1); }
  else if (k.toLowerCase() === 'f') { e.preventDefault(); toggleFull(); }
  else if (k === '+' || k === '=') { e.preventDefault(); zoomAt(z.s * 1.5, cx, cy, true); }
  else if (k === '-') { e.preventDefault(); zoomAt(z.s / 1.5, cx, cy, true); }
  else if (k === '0') { e.preventDefault(); zoomAt(1, cx, cy, true); }
}, true);
window.addEventListener('popstate', () => close(true));
