#!/usr/bin/env python3
"""The hub's navigation, defined once and written into every page's header.

Six main tabs, each page in exactly one of them; a main tab with more than one page gets one group row.
Grouped by what he does there (2026-11-22, "home under System doesn't make sense"):
  Team       what the agents did: results, the build log, Lighthouse's Today and the wall TV
  Tasks      the work
  Decisions  his say
  Business   users and money: leads, money
  Knowledge  what the agents know (its own tab with a brain icon, 2026-11-24)
AI Rank is no tab (2026-11-22, "a little flex on top"): a slim badge on Team > Results opens /rank, which sits under Team.
  System     what it runs on and costs: usage, AI jobs, machines, their power, labs

Run after changing the nav: `python3 scripts/nav.py` (add --check to only report pages that differ).
It rewrites the header of web/*.html in place and the HEADER strings of the budget and Lighthouse copies
(../budget, ../lighthouse: src/components/hub-header.tsx) when those checkouts sit next to this one.
The header stays static HTML on purpose: rendering it from JS would bring back the blink on page switch.
It also writes the three inline scripts that must run before first paint (night theme, language, the Decisions
badge), so they are defined once here; each keeps its names in a block, so no page script declares a global.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
ICONS = {
    'team': '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M16 4.6a3.2 3.2 0 0 1 0 6.8"/><path d="M18 14.2c1.8.8 3 2.7 3 5.8"/>',
    'tasks': '<rect x="3" y="3" width="18" height="18" rx="5"/><path d="m8 12 3 3 5-6"/>',
    'decisions': '<path d="M12 21v-9"/><path d="M12 12 6.5 6.5"/><path d="m12 12 5.5-5.5"/><path d="M5 10V5h5"/><path d="M19 10V5h-5"/>',
    'business': '<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
    'knowledge': '<path d="M12 5a3 3 0 1 0-6 .1 4 4 0 0 0-2.5 5.8 4 4 0 0 0 .5 6.6A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 6 .1 4 4 0 0 1 2.5 5.8 4 4 0 0 1-.5 6.6A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/><path d="M17.6 6.5a3 3 0 0 0 .4-1.4"/><path d="M6 5.1a3 3 0 0 0 .4 1.4"/><path d="M6 18a4 4 0 0 1-2-.5"/><path d="M20 17.5a4 4 0 0 1-2 .5"/>',
    'system': '<path d="M4 6h9"/><path d="M17 6h3"/><circle cx="15" cy="6" r="2"/><path d="M4 12h3"/><path d="M11 12h9"/><circle cx="9" cy="12" r="2"/><path d="M4 18h11"/><path d="M19 18h1"/><circle cx="17" cy="18" r="2"/>',
}

# the logo, first in every header: the tab icon (its windows glow) and the name, home on tap
BRAND = ('<a class="brand" href="/team" aria-label="Beacon home">'
         '<img src="/favicon.svg?v=tower2" width="28" height="28" alt=""><span>Beacon</span></a>')

# Before first paint, in <head>: the night theme (tokens.css [data-theme="cyber"]) and the language. Czech loads its
# words and i18n.js before the body parses, so the page never shows English first (README "Czech").
THEME = ('<script>try{if(localStorage.getItem("hub.theme")==="cyber"){const r=document.documentElement,'
         'm=document.querySelector(\'meta[name="theme-color"]\');r.dataset.theme="cyber";'
         'r.style.setProperty("--cy-t",-(Date.now()%60000)/1000+"s");if(m){m.dataset.plain=m.content;m.content="#0B0916"}}}'
         'catch(e){}</script>')
LANG = ('<script>window.hubLang="en";window.hubLocale="en-GB";try{if(localStorage.getItem("hub.lang")==="cs"){'
        'window.hubLang="cs";window.hubLocale="cs-CZ";'
        'document.write(\'<script src="i18n-cs.js"><\\/script><script src="i18n.js"><\\/script>\')}}catch(e){}</script>')
# Right under the header: the last Decisions count, so the badge never pops in (hub.js keeps it fresh).
BADGE = ('try{const n=Number(localStorage.getItem("hub.decisionsOpen"));'
         'if(n>0)document.getElementById("hub-decisions-badge").textContent=String(n)}catch(e){}')
BOOT = [  # (what finds the script on a page, what replaces it)
    (re.compile(r'<script>try\{if\(localStorage\.getItem\("hub\.theme"\).*?</script>'), THEME),
    (re.compile(r'<script>window\.hubLang=.*?</script>'), LANG),
    (re.compile(r'<script>try\{[^<]*hub\.decisionsOpen.*?</script>'), f'<script>{BADGE}</script>'),
]

# (key, label, [(page id, label, href)]); the first page is where the main tab goes
NAV = [
    ('team', 'Team', [('team', 'Results', '/team'), ('buildlog', 'Build log', '/buildlog'),
                      ('today', 'Today', '/lighthouse/today'), ('wall', 'Wall', '/lighthouse/dashboard')]),
    ('tasks', 'Tasks', [('tasks', 'Tasks', '/tasks')]),
    ('decisions', 'Decisions', [('decisions', 'Decisions', '/decisions'), ('inbox', 'Inbox', '/inbox')]),
    ('business', 'Business', [('outreach', 'Outreach', '/outreach'), ('money', 'Money', '/budget')]),
    ('knowledge', 'Knowledge', [('knowledge', 'Knowledge', '/knowledge')]),
    ('system', 'System', [('usage', 'Usage', '/usage'), ('ai', 'AI jobs', '/ai'), ('machines', 'Machines', '/machines'),
                          ('power', 'Power', '/power'), ('labs', 'Labs', '/labs')]),
]

# pages reached from another page, not from the nav: page id -> the page whose tab and row they show
HIDDEN = {'rank': 'team'}

# hub pages: file -> page id
PAGES = {'team.html': 'team', 'buildlog.html': 'buildlog', 'tasks.html': 'tasks', 'questions.html': 'decisions',
         'outreach.html': 'outreach', 'rank.html': 'rank', 'knowledge.html': 'knowledge', 'index.html': 'usage',
         'ai.html': 'ai', 'machines.html': 'machines', 'power.html': 'power', 'labs.html': 'labs',
         'inbox.html': 'inbox'}


def header_nav(page):
    """The main tabs, plus the group row when the page's group has more than one page."""
    apps, group = [], ''
    hidden, page = page in HIDDEN, HIDDEN.get(page, page)
    for key, label, pages in NAV:
        ids = [p[0] for p in pages]
        cur = ' aria-current="page"' if page in ids else ''
        badge = '<span class="badge" id="hub-decisions-badge"></span>' if key == 'decisions' else ''
        apps.append(f'<a class="app" href="{pages[0][2]}" data-app="{key}"{cur}>{_SVG}{ICONS[key]}</svg>'
                    f'<span>{label}</span>{badge}</a>')
        if cur and len(pages) > 1:
            links = ''.join(f'<a href="{h}" class="on" aria-current="page">{t}</a>' if pid == page and not hidden else f'<a href="{h}">{t}</a>'
                            for pid, t, h in pages)
            group = f'<nav class="sys" aria-label="{label}">{links}</nav><span class="row-break" aria-hidden="true"></span>'
    return f'{BRAND}<nav class="apps" aria-label="Hub">{"".join(apps)}</nav>{group}'


# the old header: main tabs, an optional System row, and the old Home row of tools on some pages
OLD = re.compile(r'(?:<a class="brand".*?</a>)?<nav class="apps" aria-label="Hub">.*?</nav>'
                 r'(?:<nav class="sys" aria-label="[^"]*">.*?</nav><span class="row-break" aria-hidden="true"></span>)?'
                 r'(?:\s*<nav class="tabs" aria-label="Home">.*?</nav>)?', re.S)


def write_pages(check):
    changed = []
    for name, page in PAGES.items():
        f = WEB / name
        s = f.read_text()
        new, n = OLD.subn(lambda _: header_nav(page), s, count=1)
        if n != 1:
            sys.exit(f'{name}: header not found')
        for find, script in BOOT:
            new, n = find.subn(lambda _, script=script: script, new, count=1)
            if n != 1:
                sys.exit(f'{name}: no {find.pattern[:40]}… script')
        if new != s:
            changed.append(name)
            if not check:
                f.write_text(new)
    return changed


def header_html(page):
    """The whole header for the Next.js copies, in one line (they have no page views of their own)."""
    return (f'<header class="top"> <div class="top-row"> {header_nav(page)} '
            f'<div class="tools"></div> </div> </header>')


TSX = {  # checkout -> {constant name: page id}
    'budget': {'HEADER': 'money'},
    'lighthouse': {'HEADER_TODAY': 'today', 'HEADER_WALL': 'wall'},
}


def write_tsx(check):
    changed = []
    for repo, consts in TSX.items():
        f = ROOT.parent / repo / 'src/components/hub-header.tsx'
        if not f.exists():
            print(f'skip {repo}: no checkout at {f.parent.parent.parent}')
            continue
        s = f.read_text()
        new = s
        for const, page in consts.items():
            line = re.compile(rf"^const {const} = '.*';$", re.M)
            if not line.search(new):
                sys.exit(f'{repo}: no `const {const} = \'...\';` line in {f}')
            new = line.sub(lambda _: f"const {const} = '{header_html(page)}';", new, count=1)
        badge = re.compile(r"^const BADGE = '.*';?$", re.M)
        if not badge.search(new):
            sys.exit(f"{repo}: no `const BADGE = '...';` line in {f}")
        new = badge.sub(lambda _: f"const BADGE = '{BADGE}';", new, count=1)
        if new != s:
            changed.append(repo)
            if not check:
                f.write_text(new)
    return changed


if __name__ == '__main__':
    check = '--check' in sys.argv
    out = write_pages(check) + write_tsx(check)
    print(('differ: ' if check else 'written: ') + (', '.join(out) or 'nothing'))
    sys.exit(1 if check and out else 0)
