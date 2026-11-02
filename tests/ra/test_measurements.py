"""Small, hand-checkable day 11 inputs and actual operation counters."""

from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from dbms.ra.evaluate import execute
from dbms.ra.generator import generate
from dbms.ra.operators import Statistics
from dbms.ra.relfile import load_relations


ROOT = Path(__file__).resolve().parents[2]
ENV = {'PYTHONPATH': str(ROOT)}


def module(name):
    return [sys.executable, '-B', '-m', name]


def tables(n, m, k):
    text = StringIO()
    generate(text, n, m, k)
    return load_relations(text.getvalue())


class MeasurementTests(unittest.TestCase):
    def test_generator_sizes_and_match_rates(self):
        for n, m, k in [(3, 4, 0), (3, 4, 1), (3, 4, 2), (3, 4, 4), (0, 4, 2), (3, 0, 0)]:
            with self.subTest(n=n, m=m, k=k):
                data = tables(n, m, k)
                self.assertEqual(len(data['R'].rows), n)
                self.assertEqual(len(data['S'].rows), m)
                self.assertEqual([c.name for c in data['R'].columns], ['a', 'b'])
                self.assertEqual([c.name for c in data['S'].columns], ['b', 'c'])
                stats = Statistics()
                result = execute('R join[R.b=S.b] S', data, stats)
                self.assertEqual(len(result.rows), n * k)
                self.assertEqual(stats.join_pairs, n * m)
                self.assertEqual(stats.selection_rows, 0)
                self.assertEqual(stats.output_rows, n * k)
                self.assertGreaterEqual(stats.wall_seconds, 0)

    def test_generator_reproducible_and_rejects_invalid_settings(self):
        a, b = StringIO(), StringIO()
        generate(a, 3, 4, 2)
        generate(b, 3, 4, 2)
        self.assertEqual(a.getvalue(), b.getvalue())
        for args in [(-1, 2, 0), (2, -1, 0), (2, 3, -1), (2, 3, 4)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                generate(StringIO(), *args)

    def test_nested_selections_count_actual_inputs(self):
        stats = Statistics()
        result = execute('select[a=0](select[a<2](R))', tables(3, 4, 1), stats)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(stats.selection_rows, 5)  # 3 inner rows, then 2 outer rows.
        self.assertEqual(stats.join_pairs, 0)

    def test_join_matches_materialized_product_then_selection(self):
        data = tables(3, 4, 2)
        for condition in ('R.b=S.b', 'R.a<S.c', 'R.a=0 or S.c=3',
                          'not (R.b=S.b and R.a>=1)'):
            with self.subTest(condition=condition):
                stats = Statistics()
                result = execute(f'R join[{condition}] S', data, stats)
                expected = execute(f'select[{condition}](R times S)', data)
                self.assertEqual(result, expected)
                self.assertEqual((stats.join_pairs, stats.selection_rows), (12, 0))

    def test_join_after_filter_uses_filtered_input(self):
        stats = Statistics()
        execute('select[a<2](R) join[R.b=S.b] S', tables(3, 4, 1), stats)
        self.assertEqual(stats.join_pairs, 8)
        self.assertEqual(stats.selection_rows, 3)  # Only the explicit filter examines rows.

    def test_multiple_joins_accumulate(self):
        stats = Statistics()
        execute('(R join[R.b=S.b] S) join[R.b=T.b] rename[T](S)', tables(3, 4, 2), stats)
        self.assertEqual(stats.join_pairs, 36)  # 3*4, then 6*4.
        self.assertEqual(stats.output_rows, 12)

    def test_measurements_reset_and_do_not_change_results(self):
        data, stats = tables(3, 4, 2), Statistics()
        query = 'R join[R.b=S.b] S'
        self.assertEqual(execute(query, data), execute(query, data, stats))
        # Bag semantics: projection keeps all three R rows (Bonus 1 gave 1).
        execute('project[b](R)', data, stats)
        self.assertEqual((stats.join_pairs, stats.selection_rows, stats.output_rows), (0, 0, 3))
        execute('R times S', data, stats)
        self.assertEqual((stats.join_pairs, stats.selection_rows), (0, 0))

    def test_timer_wraps_evaluation(self):
        stats = Statistics()
        with patch('dbms.ra.evaluate.perf_counter', side_effect=[10.0, 10.25]):
            execute('R', tables(3, 4, 1), stats)
        self.assertEqual(stats.wall_seconds, 0.25)

    def test_cli_generator_and_statistics(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'input.txt')
            command = [*module('dbms.ra.generator'), '--r-rows', '3',
                       '--s-rows', '4', '--matches', '2', '--output', path]
            generated = subprocess.run(command, capture_output=True, text=True, timeout=10, env=ENV)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            result = subprocess.run([*module('dbms.ra.cli'), '--relations', path,
                                     '--query', 'R join[R.b=S.b] S', '--stats'],
                                    capture_output=True, text=True, timeout=10, env=ENV)
            self.assertEqual(result.returncode, 0, result.stderr)
            stats = json.loads(result.stderr)
            self.assertEqual((stats['join_pairs'], stats['selection_rows'], stats['output_rows']), (12, 0, 6))
            self.assertIn('(R.a, R.b, S.b, S.c)', result.stdout)
            original = Path(path).read_bytes()
            repeated = subprocess.run(command, capture_output=True, text=True, timeout=10, env=ENV)
            self.assertEqual(repeated.returncode, 1)
            self.assertEqual(Path(path).read_bytes(), original)
            self.assertNotIn('Traceback', repeated.stderr)

    def test_cli_rejects_invalid_options(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'input.txt')
            result = subprocess.run([*module('dbms.ra.generator'),
                                     '--r-rows', '3', '--s-rows', '4', '--matches', '5', '--output', path],
                                    capture_output=True, text=True, timeout=10, env=ENV)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(Path(path).exists())
        result = subprocess.run([*module('dbms.ra.cli'), '--tree', 'R', '--stats'],
                                capture_output=True, text=True, timeout=10, env=ENV)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--stats requires --query', result.stderr)


if __name__ == '__main__':
    unittest.main()
