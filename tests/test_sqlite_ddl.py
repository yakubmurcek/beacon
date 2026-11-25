"""The reverse-copy tool's SQLite schema (ts/packages/db/src/sqlite-ddl.ts) is the one the hub creates today."""
import os
import subprocess
import sys
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'sqlite_ddl.py')


class SqliteDdlTest(unittest.TestCase):
    def test_captured_schema_matches_the_hub(self):
        run = subprocess.run([sys.executable, SCRIPT, '--check'], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
