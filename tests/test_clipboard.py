"""Every copy button goes through shared.js copyText.

Beacon is plain http on the tailnet, where navigator.clipboard does not exist, so a page calling it directly
shows "Copy failed" (the Outreach mail's Copy text did, 2026-11-25). copyText falls back to execCommand.
"""
import glob
import os
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')


class Clipboard(unittest.TestCase):
    def test_only_shared_touches_the_clipboard_api(self):
        bad = []
        for path in sorted(glob.glob(os.path.join(WEB, '*.js'))):
            if os.path.basename(path) == 'shared.js':
                continue
            with open(path, encoding='utf-8') as f:
                for n, line in enumerate(f, 1):
                    if 'navigator.clipboard' in line and not line.lstrip().startswith('//'):
                        bad.append(f'{os.path.basename(path)}:{n}')
        self.assertEqual(bad, [], 'use copyText from shared.js: ' + ', '.join(bad))


if __name__ == '__main__':
    unittest.main()
