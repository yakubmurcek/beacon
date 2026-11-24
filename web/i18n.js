// Czech, an optional switch next to English (the header's CZ / EN button, hub.js).
// The pages are written in English; with Czech on, the head script on each page loads this file before the body
// parses, and it rewrites the hub's own words as the page draws them. Only whole strings are swapped: a text node or
// a placeholder / title / aria-label that matches an entry exactly, or one of the patterns with numbers in it.
// Data the agents write (task titles, narration, card text) never matches a whole entry, so it stays as written.
// The words live in i18n-cs.js (window.HUB_CS: exact entries and {name} patterns).
(function () {
  const root = document.documentElement;
  root.lang = 'cs';
  const DICT = window.HUB_CS || { exact: {}, patterns: {} };
  const exact = new Map(Object.entries(DICT.exact));
  const rules = new Intl.PluralRules('cs');

  // "{n} tasks open" -> /^(.+?) tasks open$/ ; Czech side "{n|úkol|úkoly|úkolů} otevřeno" picks the plural form
  const esc = (s) => s.replace(/[.*+?^$()|[\]\\]/g, '\\$&');
  const patterns = Object.entries(DICT.patterns).map(([en, cs]) => {
    const names = [];
    const src = esc(en).replace(/\\?\{(\w+)\\?\}/g, (_, name) => { names.push(name); return /^n\d?$/.test(name) ? '([\\d.,\\s]+)' : '(.+?)'; });
    return { re: new RegExp('^' + src + '$'), names, cs };
  });
  function fill(cs, vals) {
    return cs.replace(/\{(\w+)(?:\|([^}]*))?\}/g, (all, name, forms) => {
      const v = vals[name];
      if (v === undefined) return all;
      if (!forms) return tr(v) ?? v;
      const [one, few, many] = forms.split('|');
      const num = parseFloat(String(v).replace(/[\s,]/g, ''));
      const cat = Number.isInteger(num) ? rules.select(num) : 'many';
      return v + ' ' + (cat === 'one' ? one : cat === 'few' ? few : many);
    });
  }
  function tr(text) {
    const hit = exact.get(text);
    if (hit !== undefined) return hit;
    for (const p of patterns) {
      const m = p.re.exec(text);
      if (!m) continue;
      const vals = {};
      p.names.forEach((name, i) => { vals[name] = m[i + 1]; });
      return fill(p.cs, vals);
    }
    // "CPU 4% · load 1.2 · 16 cores": a run of parts, each swapped on its own
    if (text.includes(' · ')) {
      const parts = text.split(' · '), out = parts.map((x) => tr(x) ?? x);
      if (out.some((x, i) => x !== parts[i])) return out.join(' · ');
    }
    return null;
  }
  window.hubT = (text) => tr(text) ?? text;

  const SKIP = 'script,style,textarea,input,code,pre,[contenteditable],[translate="no"]';
  const done = new WeakSet();
  function text(node) {
    if (done.has(node)) { done.delete(node); return; }  // our own write coming back as a mutation
    const v = node.nodeValue, t = v.trim();
    if (!t || t.length > 600 || !/[A-Za-z]/.test(t)) return;
    const p = node.parentElement;
    if (!p || p.closest(SKIP)) return;
    const out = tr(t.replace(/\s+/g, ' '));
    if (out === null || out === t) return;
    done.add(node);
    node.nodeValue = v.replace(t, out);
  }
  const ATTRS = ['placeholder', 'title', 'aria-label'];
  function attrs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v) continue;
      const out = tr(v.trim());
      if (out !== null && out !== v) el.setAttribute(a, out);
    }
  }
  function walk(node) {
    if (node.nodeType === 3) { text(node); return; }
    if (node.nodeType !== 1 || node.matches('script,style')) return;
    attrs(node);
    const it = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n2 = it.nextNode(); n2; n2 = it.nextNode()) {
      if (n2.nodeType === 3) text(n2); else attrs(n2);
    }
  }
  new MutationObserver((list) => {
    for (const m of list) {
      if (m.type === 'childList') m.addedNodes.forEach(walk);
      else if (m.type === 'characterData') text(m.target);
      else if (m.type === 'attributes') {
        const el = /** @type {Element} */ (m.target);
        const out = tr((el.getAttribute(m.attributeName) || '').trim());
        if (out !== null && out !== el.getAttribute(m.attributeName)) el.setAttribute(m.attributeName, out);
      }
    }
  }).observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  walk(root);
})();
