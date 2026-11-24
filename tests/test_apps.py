import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hub'))
import apps  # noqa: E402


class AppsTest(unittest.TestCase):
    def test_only_the_app_prefixes_pass_through(self):
        self.assertEqual(apps.backend_for('/budget'), apps.PREFIXES['/budget'])
        self.assertEqual(apps.backend_for('/budget/import'), apps.PREFIXES['/budget'])
        self.assertEqual(apps.backend_for('/lighthouse/_next/static/x.js'), apps.PREFIXES['/lighthouse'])
        for path in ('/budgets', '/power', '/api/power', '/tasks', '/lighthousekeeper', '/'):
            self.assertIsNone(apps.backend_for(path), path)


if __name__ == '__main__':
    unittest.main()
