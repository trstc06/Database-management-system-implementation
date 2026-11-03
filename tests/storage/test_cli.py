"""The storage tool through its command line (Section 7 and the checklist)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class StorageCommandTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.db = str(self.root / 'db')

    def tearDown(self):
        self.folder.cleanup()

    def storage(self, *args, expect=0):
        result = subprocess.run([sys.executable, '-m', 'dbms.storage.cli', *args],
                                capture_output=True, text=True, timeout=60,
                                env={**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(result.returncode, expect, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return result

    def file(self, name, text):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return str(path)

    def load_rows(self, count):
        self.storage('create-table', '--db', self.db, '--name', 'R', '--schema', 'a:INT,b:VARCHAR(20),c:INT')
        csv = self.file('r.csv', ''.join(f'{i},name{i % 7},{i * 3}\n' for i in range(count)))
        self.storage('load', '--db', self.db, '--table', 'R', '--file', csv)

    def test_scan_json_shape(self):
        self.load_rows(2)
        result = json.loads(self.storage('scan', '--db', self.db, '--table', 'R', '--frames', '8', '--json').stdout)
        self.assertEqual(result['table'], 'R')
        self.assertEqual(result['schema'], [['a', 'INT'], ['b', 'VARCHAR', 20], ['c', 'INT']])
        self.assertEqual(result['rows'], [[0, 'name0', 0], [1, 'name1', 3]])
        self.assertEqual(set(result['stats']), {'hits', 'misses', 'evictions', 'reads', 'writes'})

    def test_trace_json_reproduces_section_5_3(self):
        trace = self.file('trace.txt', '1\n2\n3\n1\n4\n2\n')
        expected = {'lru': (1, 5, 2), 'clock': (2, 4, 1)}
        for policy, (hits, misses, evictions) in expected.items():
            with self.subTest(policy=policy):
                result = json.loads(self.storage('trace', '--db', self.db, '--file', trace, '--frames', '3',
                                                 '--policy', policy, '--json').stdout)
                self.assertEqual(result, {'policy': policy, 'frames': 3, 'hits': hits, 'misses': misses,
                                          'evictions': evictions, 'reads': misses, 'writes': 0})

    def test_same_command_twice_gives_identical_counters(self):
        self.load_rows(3000)
        args = ('scan', '--db', self.db, '--table', 'R', '--frames', '4', '--json')
        first, second = self.storage(*args).stdout, self.storage(*args).stdout
        self.assertEqual(first, second)
        stats = json.loads(first)['stats']
        pages = json.loads(self.storage('layout', '--db', self.db, '--table', 'R', '--page', '0', '--json').stdout)
        self.assertEqual(stats['reads'], 1 + pages['entry_count'])  # directory page plus each data page once

    def test_limit_stops_the_scan(self):
        self.load_rows(1000)
        result = json.loads(self.storage('scan', '--db', self.db, '--table', 'R', '--limit', '3', '--json').stdout)
        self.assertEqual(len(result['rows']), 3)
        self.assertEqual(result['stats']['reads'], 2)

    def test_layout_reports_slots(self):
        self.load_rows(3)
        layout = json.loads(self.storage('layout', '--db', self.db, '--table', 'R', '--page', '1', '--json').stdout)
        self.assertEqual(layout['kind'], 'data')
        self.assertEqual(layout['slot_count'], 3)
        self.assertEqual(layout['slots'][0]['offset'] + layout['slots'][0]['length'], 4096)
        self.assertIn('free_end', self.storage('layout', '--db', self.db, '--table', 'R', '--page', '1').stdout)

    def test_errors_use_one_format_and_exit_1(self):
        self.load_rows(1)
        cases = [('scan', '--db', self.db, '--table', 'Nope'),
                 ('create-table', '--db', self.db, '--name', 'S', '--schema', 'a:FLOAT'),
                 ('load', '--db', self.db, '--table', 'R', '--file', self.file('bad.csv', '1,x,y\n')),
                 ('layout', '--db', self.db, '--table', 'R', '--page', '9'),
                 ('trace', '--file', self.file('bad.txt', '1\nx\n'))]
        for args in cases:
            with self.subTest(args=args[0]):
                self.assertTrue(self.storage(*args, expect=1).stderr.startswith('error: '))


if __name__ == '__main__':
    unittest.main()
