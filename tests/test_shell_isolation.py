"""A page's own stylesheet can't reach the shared shell (header, tabs, Decisions badge, feedback pen).

Every page sits inside its body class (`.inbox-page`), and so does the header. A page rule like `.inbox-page .ping`
therefore also hit the header's Decisions badge whenever it carried its short-lived `ping` class: the badge
stretched to a wide pill on Inbox, only after a new decision arrived, so no screenshot at rest ever showed it
(2026-11-24). This test finds every class the shell can carry and fails when a page sheet styles one of them.
"""
import glob
import os
import re
import unittest
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
SHELL_SHEETS = {'tokens.css', 'app.css', 'hub.css', 'hub-shell.css'}  # the shell's own look lives here
SHELL_SCRIPTS = ('hub.js', 'feedback.js', 'shared.js')  # they build or decorate shell elements at run time


class _Classes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sets, self.depth = [], 0

    def handle_starttag(self, tag, attrs):
        if tag == 'header':
            self.depth += 1
        if self.depth:
            self.sets.append(frozenset(dict(attrs).get('class', '').split()))

    def handle_endtag(self, tag):
        if tag == 'header' and self.depth:
            self.depth -= 1


def shell_class_sets():
    """Class sets of the shared shell: header elements on two or more pages, plus what the shell scripts make.

    A header part only one page has (Usage's date picker) is that page's own, so its sheet may style it.
    """
    seen = {}
    for path in glob.glob(os.path.join(WEB, '*.html')):
        p = _Classes()
        with open(path, encoding='utf-8') as f:
            p.feed(f.read())
        for s in set(p.sets):
            if s:
                seen[s] = seen.get(s, 0) + 1
    sets = {s for s, n in seen.items() if n >= 2}
    for name in SHELL_SCRIPTS:
        with open(os.path.join(WEB, name), encoding='utf-8') as f:
            js = f.read()
        for m in re.finditer(r"""class(?:Name)?\s*[:=]\s*\\?['"]([\w\s-]+)\\?['"]""", js):
            sets.add(frozenset(m.group(1).split()))
        # a class added at run time can land on any shell element (the badge's ping did)
        for m in re.finditer(r"""classList\.(?:add|toggle)\(\s*['"]([\w-]+)['"]""", js):
            sets.add(frozenset([m.group(1)]))
    return sets


def rules(css):
    """(selector group, line) for every style rule, @keyframes bodies left out."""
    css = re.sub(r'/\*.*?\*/', lambda m: '\n' * m.group(0).count('\n'), css, flags=re.S)
    css = re.sub(r'@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}', lambda m: '\n' * m.group(0).count('\n'), css)
    for m in re.finditer(r'([^{};]+)\{', css):
        sel = m.group(1).strip()
        if sel and not sel.startswith('@'):
            yield sel, css.count('\n', 0, m.end(1) - len(m.group(1).lstrip()) + 0) + 1


# compounds that only pick the page or theme, so they match the header too: .inbox-page, :root[...], html, body
ROOT_LEVEL = re.compile(r'^(:root|html|body)?(\[[^\]]*\]|\.[\w-]+-page|:[\w-]+(\([^()]*\))?)*$')


def accidental_subject(selector):
    """The subject's classes when everything before it only picks the page or theme, else None.

    `.inbox-page .ping` reaches whatever carries `ping` anywhere, header included: accidental.
    `.outreach-page .top .tabs` names the header on purpose: fine.
    """
    parts = [p for p in re.split(r'\s*[>+~]\s*|\s+', re.sub(r'\([^()]*\)', '()', selector.strip())) if p]
    if not parts or not all(ROOT_LEVEL.match(p) for p in parts[:-1]):
        return None
    return frozenset(re.findall(r'\.([\w-]+)', re.sub(r'::?[\w-]+(\(\))?', '', parts[-1]))) or None


class ShellIsolation(unittest.TestCase):
    def test_page_sheets_do_not_style_the_shell(self):
        shell = shell_class_sets()
        self.assertTrue(any('badge' in s for s in shell), 'the header was not found; update this test')
        for path in sorted(glob.glob(os.path.join(WEB, '*.css'))):
            name = os.path.basename(path)
            if name in SHELL_SHEETS:
                continue
            with open(path, encoding='utf-8') as f:
                css = f.read()
            for group, line in rules(css):
                for sel in group.split(','):
                    want = accidental_subject(sel)
                    hit = want and next((s for s in shell if want <= s), None)
                    with self.subTest(sheet=name, selector=sel.strip()):
                        self.assertFalse(hit, f'{name}:{line}: "{sel.strip()}" also styles the shared header element with '
                                              f'class "{" ".join(sorted(hit or []))}". Rename the page\'s class.')

    def test_the_inbox_clash_is_caught(self):
        # the bug this test exists for, kept as a fixed example so the detector can't silently go blind
        shell = shell_class_sets() | {frozenset(['badge', 'ping'])}
        want = accidental_subject('.inbox-page .ping')
        self.assertTrue(any(want <= s for s in shell))


if __name__ == '__main__':
    unittest.main()
