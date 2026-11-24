"""The design system holds: colours live only in web/tokens.css, pages load one stack of sheets, no override layers.

Earlier clean-ups regressed because each one stacked a new skin over the last (brand.css, then cyber.css beating it
with `:not(#x)`). These checks keep the single layer: tokens.css -> app.css -> the page's sheet -> hub.css.
"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
COLOUR = re.compile(r'#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(\s*\d')
# tokens.css defines the colours; hub-shell.css carries a copy of the Paper ones for the budget and Lighthouse apps
COLOUR_HOMES = {'tokens.css', 'hub-shell.css'}


def sheets():
    for path in sorted(glob.glob(os.path.join(WEB, '*.css'))):
        with open(path, encoding='utf-8') as f:
            yield os.path.basename(path), re.sub(r'/\*.*?\*/', '', f.read(), flags=re.S)


def tokens(text, selector):
    """name -> value for the custom properties in the first block whose selector starts with `selector`."""
    start = text.index(selector)
    block = text[text.index('{', start) + 1:text.index('}', start)]
    return {m.group(1): ' '.join(m.group(2).split()) for m in re.finditer(r'(--[\w-]+):\s*([^;]+);', block)}


class DesignSystem(unittest.TestCase):
    def test_colours_only_in_tokens(self):
        for name, css in sheets():
            if name in COLOUR_HOMES:
                continue
            for line in css.splitlines():
                with self.subTest(sheet=name, line=line.strip()[:80]):
                    self.assertIsNone(COLOUR.search(line), f'{name}: use a token from tokens.css, not a raw colour')

    def test_every_stylesheet_has_balanced_braces(self):
        # one stray '}' silently drops the next rule (2026-11-24: the lead grid fell to one column)
        for path in sorted(glob.glob(os.path.join(WEB, '*.css'))):
            depth = 0
            with open(path, encoding='utf8') as f:
                text = re.sub(r'/\*.*?\*/', '', f.read(), flags=re.S)
            for n, line in enumerate(text.split('\n'), 1):
                for ch in line:
                    depth += {'{': 1, '}': -1}.get(ch, 0)
                    self.assertGreaterEqual(depth, 0, f'{os.path.basename(path)}:{n}: a closing brace too many')
            self.assertEqual(depth, 0, f'{os.path.basename(path)}: an unclosed brace')

    def test_no_override_layers(self):
        for name, css in sheets():
            with self.subTest(sheet=name):
                self.assertNotIn(':not(#x)', css, f'{name}: specificity hacks mean a second skin is fighting the first')
        for gone in ('brand.css', 'cyber.css'):
            self.assertFalse(os.path.exists(os.path.join(WEB, gone)), f'{gone} came back: put the look in tokens.css')

    def test_pages_load_one_stack(self):
        for path in sorted(glob.glob(os.path.join(WEB, '*.html'))):
            with open(path, encoding='utf-8') as f:
                links = re.findall(r'<link rel="stylesheet" href="([^"]+)"', f.read())
            name = os.path.basename(path)
            with self.subTest(page=name):
                self.assertGreaterEqual(len(links), 3, name)
                self.assertEqual(links[:2], ['tokens.css', 'app.css'], f'{name}: tokens.css then app.css come first')
                self.assertEqual(links[-1], 'hub.css', f'{name}: hub.css (the header) comes last')

    def test_shell_copy_matches_tokens(self):
        with open(os.path.join(WEB, 'tokens.css'), encoding='utf-8') as f:
            paper = tokens(re.sub(r'/\*.*?\*/', '', f.read(), flags=re.S), ':root {')
        with open(os.path.join(WEB, 'hub-shell.css'), encoding='utf-8') as f:
            shell = tokens(re.sub(r'/\*.*?\*/', '', f.read(), flags=re.S), '.top, .top .apps {')
        skip = {'--glass', '--sel-fg', '--sel-bg'}  # resolved there: the other apps have no --bg or --text to point at
        for name, value in shell.items():
            if name in skip or name not in paper or 'var(' in paper[name]:
                continue
            with self.subTest(token=name):
                self.assertEqual(value.lower(), paper[name].lower(), f'hub-shell.css {name} drifted from tokens.css')

    def test_night_backdrop_stays_animated(self):
        # the owner asked for it back after a clean-up stilled it: drifting glows, a rolling floor, the network
        with open(os.path.join(WEB, 'app.css'), encoding='utf-8') as f:
            css = f.read()
        for name in ('cy-drift', 'cy-floor'):
            self.assertIn(f'animation: {name}', css, f'Night backdrop lost its {name} animation')
        with open(os.path.join(WEB, 'hub.js'), encoding='utf-8') as f:
            self.assertIn("canvas.id = 'cyber-bg'", f.read(), 'Night backdrop lost its node network')


if __name__ == '__main__':
    unittest.main()
