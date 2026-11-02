"""Operators pull from any source (point 5) and work on bags (point 6)."""

import ast
import unittest
from pathlib import Path
from dbms.common.errors import ExecutionError
from dbms.common.schema import INT, VARCHAR, Column
from dbms.ra import operators as ops
from dbms.ra.parser import parse_condition

PACKAGE = Path(__file__).resolve().parents[2] / 'dbms' / 'ra'


class CountingSource:
    """A source that is not an in-memory table and counts its passes."""

    def __init__(self, qualifier, rows):
        self.columns = (Column(qualifier, 'x', INT), Column(qualifier, 'y', VARCHAR))
        self.rows = rows
        self.scans = 0

    def scan(self):
        self.scans += 1
        yield from self.rows


def rows(source):
    return list(source.scan())


class SourceTests(unittest.TestCase):
    def test_operators_accept_any_source_and_stay_lazy(self):
        source = CountingSource('R', [(1, 'a'), (2, 'b')])
        selected = ops.select(source, parse_condition('x > 1'))
        self.assertEqual(source.scans, 0)  # building the operator reads nothing
        self.assertEqual(rows(selected), [(2, 'b')])
        self.assertEqual(rows(selected), [(2, 'b')])  # each scan is a fresh pass
        self.assertEqual(source.scans, 2)

    def test_errors_raised_before_reading(self):
        source = CountingSource('R', [(1, 'a')])
        with self.assertRaises(ExecutionError):
            ops.select(source, parse_condition("x = 'a'"))
        with self.assertRaises(ExecutionError):
            ops.project(source, ('missing',))
        self.assertEqual(source.scans, 0)

    def test_join_rescans_inner_source_per_outer_row(self):
        left = CountingSource('R', [(1, 'a'), (2, 'b'), (1, 'c')])
        right = CountingSource('S', [(1, 'z'), (1, 'w')])
        joined = ops.join(left, right, parse_condition('R.x = S.x'))
        self.assertEqual(len(rows(joined)), 4)
        self.assertEqual((left.scans, right.scans), (1, 3))


class BagTests(unittest.TestCase):
    def setUp(self):
        self.r = CountingSource('R', [(1, 'a'), (1, 'a'), (2, 'b')])
        self.s = CountingSource('S', [(1, 'a'), (3, 'c'), (3, 'c')])

    def test_projection_keeps_duplicates(self):
        self.assertEqual(rows(ops.project(self.r, ('y',))), [('a',), ('a',), ('b',)])
        self.assertEqual(rows(ops.distinct(ops.project(self.r, ('y',)))), [('a',), ('b',)])

    def test_union_all_versus_union(self):
        self.assertEqual(len(rows(ops.union_all(self.r, self.s))), 6)
        self.assertEqual(rows(ops.union(self.r, self.s)), [(1, 'a'), (2, 'b'), (3, 'c')])

    def test_intersect_and_except_are_sets_even_with_duplicate_inputs(self):
        self.assertEqual(rows(ops.intersect(self.r, self.s)), [(1, 'a')])
        self.assertEqual(rows(ops.except_(self.r, self.s)), [(2, 'b')])
        self.assertEqual(rows(ops.except_(self.s, self.r)), [(3, 'c')])

    def test_set_output_takes_left_names(self):
        self.assertEqual([c.qualified_name for c in ops.union_all(self.r, self.s).columns], ['R.x', 'R.y'])


class LayerTests(unittest.TestCase):
    def test_library_modules_never_print_or_open_files(self):
        library = ['tokenizer.py', 'parser.py', 'tree.py', 'conditions.py', 'operators.py',
                   'serialize.py', 'relfile.py', 'evaluate.py']
        for name in library:
            with self.subTest(module=name):
                tree = ast.parse((PACKAGE / name).read_text(encoding='utf-8'))
                calls = {node.func.id for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
                self.assertFalse(calls & {'print', 'open', 'input'})

    def test_four_layers_import_independently(self):
        for module in ['tokenizer', 'parser', 'tree', 'operators']:
            with self.subTest(module=module):
                __import__(f'dbms.ra.{module}')


if __name__ == '__main__':
    unittest.main()
