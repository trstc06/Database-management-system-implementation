"""The sql command: parse, compile and check, with real catalogs."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from dbms.sql.scope import load_catalog
from tests.execution.helpers import make_db
from tests.sql.helpers import TABLES

ROOT = Path(__file__).resolve().parents[2]


def sql(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / 'bin' / 'sql'), *args], capture_output=True, text=True)


class SqlCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp())
        cls.catalog = cls.work / 'catalog.json'
        cls.catalog.write_text(json.dumps({'tables': TABLES}), encoding='utf-8')

    def write(self, text: str) -> str:
        path = self.work / 'q.sql'
        path.write_text(text, encoding='utf-8')
        return str(path)

    def test_parse_explain_needs_no_catalog_and_shows_union_kind(self):
        result = sql('parse', '--query', 'SELECT city FROM Emp UNION ALL SELECT city FROM Dept', '--explain')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines()[0], 'union_all')

    def test_compile_json_matches_the_plan(self):
        result = sql('compile', '--db', str(self.catalog), '--file', self.write('SELECT name FROM Emp;'), '--json')
        self.assertEqual(json.loads(result.stdout),
                         {'op': 'project', 'columns': ['Emp.name'], 'child': {'op': 'scan', 'table': 'Emp'}})

    def test_create_then_select_in_one_file(self):
        text = "CREATE TABLE T (a INT, b VARCHAR(5));\nINSERT INTO T VALUES (1, 'x');\nSELECT b FROM T;"
        result = sql('compile', '--db', str(self.catalog), '--file', self.write(text), '--json')
        plans = json.loads(result.stdout)
        self.assertEqual([p['op'] for p in plans], ['create_table', 'insert', 'project'])

    def test_check_exit_codes_and_error_format(self):
        good = sql('check', '--db', str(self.catalog), '--file', self.write('SELECT name FROM Emp;'))
        self.assertEqual((good.returncode, good.stdout), (0, ''))
        bad = sql('check', '--db', str(self.catalog), '--file', self.write('SELECT\n  did FROM Emp, Dept;'))
        self.assertEqual(bad.returncode, 1)
        self.assertTrue(bad.stderr.startswith('error: ambiguity: '))
        self.assertTrue(bad.stderr.rstrip().endswith('at line 2, column 3'))
        self.assertNotIn('Traceback', bad.stderr)

    def test_catalog_from_a_database_folder(self):
        db = make_db({'R': (['a:INT', 'b:VARCHAR(8)'], [(1, 'x')])})
        self.assertEqual([c.name for c in load_catalog(str(db))['R']], ['a', 'b'])
        result = sql('compile', '--db', str(db), '--query', 'SELECT b FROM R WHERE a = 1', '--json')
        self.assertEqual(json.loads(result.stdout)['columns'], ['R.b'])


if __name__ == '__main__':
    unittest.main()
