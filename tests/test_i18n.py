"""Czech (web/i18n.js + web/i18n-cs.js): every page can switch, and the word list stays loadable."""
import json
import pathlib
import re
import shutil
import subprocess
import unittest

WEB = pathlib.Path(__file__).resolve().parent.parent / 'web'


class Czech(unittest.TestCase):
    def test_every_page_loads_the_switch_before_the_body(self):
        for f in WEB.glob('*.html'):
            head = f.read_text().split('<body', 1)[0]
            self.assertIn('localStorage.getItem("hub.lang")', head, f'{f.name}: no Czech loader in the head')

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_word_list_parses_and_placeholders_match(self):
        out = subprocess.run(['node', '-e', 'global.window={};require(process.argv[1]);console.log(JSON.stringify(window.HUB_CS))',
                              str(WEB / 'i18n-cs.js')], capture_output=True, text=True, check=True).stdout
        cs = json.loads(out)
        self.assertGreater(len(cs['exact']), 500)
        for en, tr in cs['patterns'].items():
            names = set(re.findall(r'\{(\w+)\}', en))
            used = set(re.findall(r'\{(\w+)(?:\|[^}]*)?\}', tr))
            self.assertEqual(names, used, f'pattern {en!r}: placeholders differ in Czech')
            for forms in re.findall(r'\{\w+\|([^}]*)\}', tr):
                self.assertEqual(len(forms.split('|')), 3, f'pattern {en!r}: plural needs one|few|many')


if __name__ == '__main__':
    unittest.main()
