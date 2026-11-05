"""The worked examples in Sections 2, 3 and 5.1."""

import math
import unittest
from dbms.optimizer import estimate as est
from dbms.optimizer.planner import Planner
from dbms.optimizer.rewrite import rewrite
from dbms.optimizer.stats import ColumnStats, TableStats
from dbms.ra.parser import parse_condition
from tests.sql.helpers import TABLES, plan

COLUMN_NAMES = {name: [c[0] for c in columns] for name, columns in TABLES.items()}


def scan(table: str, alias: str) -> dict:
    return {'op': 'rename', 'to': alias, 'child': {'op': 'scan', 'table': table}}


class SelectivityExample(unittest.TestCase):
    """Emp: 10,000 tuples, ndv(did) = 50, age uniform on 20..70. Dept: 50 tuples."""

    def setUp(self):
        emp = TableStats('Emp', 10000, 200, 40, {'did': ColumnStats(50, 4, 1, 50), 'age': ColumnStats(51, 4, 20, 70)})
        dept = TableStats('Dept', 50, 1, 40, {'did': ColumnStats(50, 4, 1, 50)})
        self.emp = est.table_estimate(emp, ['did', 'age'], 'Emp')
        self.dept = est.table_estimate(dept, ['did'], 'Dept')

    def rows(self, condition: str) -> int:
        return est.filtered(self.emp, parse_condition(condition)).rows

    def test_each_line_of_the_example(self):
        self.assertEqual(self.rows('Emp.did = 7'), 200)
        self.assertEqual(self.rows('Emp.age > 60'), 2000)
        self.assertEqual(self.rows('Emp.did = 7 and Emp.age > 60'), 40)
        joined = est.joined(self.emp, self.dept, parse_condition('Emp.did = Dept.did'))
        self.assertEqual(joined.rows, 10000)

    def test_output_pages_follow_width(self):
        narrow = est.projected(self.emp, ['Emp.did'])
        self.assertEqual(narrow.pages, math.ceil(10000 * 4 / 4096))


class RewriteExample(unittest.TestCase):
    def test_section_3_before_and_after(self):
        logical = plan('SELECT e.name FROM Emp e, Dept d WHERE e.did = d.did AND e.age > 60 AND d.budget > 1000;')
        self.assertEqual(rewrite(logical, COLUMN_NAMES), {
            'op': 'project', 'columns': ['e.name'], 'child': {
                'op': 'join', 'condition': 'e.did = d.did',
                'left': {'op': 'project', 'columns': ['e.name', 'e.did'],
                         'child': {'op': 'select', 'condition': 'e.age > 60', 'child': scan('Emp', 'e')}},
                'right': {'op': 'project', 'columns': ['d.did'],
                          'child': {'op': 'select', 'condition': 'd.budget > 1000', 'child': scan('Dept', 'd')}}}})


class DpTableExample(unittest.TestCase):
    """Section 5.1: A 100 pages, B' 10 pages, C 400 pages, B = 32."""

    def setUp(self):
        self.stats = {
            'A': TableStats('A', 5000, 100, 40, {'x': ColumnStats(2500, 4, None, None)}),
            'Bp': TableStats('Bp', 500, 10, 41, {'x': ColumnStats(500, 4, None, None), 'y': ColumnStats(500, 4, None, None)}),
            'C': TableStats('C', 20000, 400, 40, {'y': ColumnStats(5000, 4, None, None)}),
        }
        self.tables = {'A': ['x'], 'Bp': ['x', 'y'], 'C': ['y']}
        self.logical = {'op': 'join', 'condition': 'Bp.y = C.y',
                        'left': {'op': 'join', 'condition': 'A.x = Bp.x',
                                 'left': {'op': 'scan', 'table': 'A'}, 'right': {'op': 'scan', 'table': 'Bp'}},
                        'right': {'op': 'scan', 'table': 'C'}}

    def test_best_plan_per_subset(self):
        planner = Planner(self.tables, self.stats, 32)
        planner.plan(self.logical)
        best = {row.subset: (row.plan, row.cost) for row in planner.dp_tables[0] if row.best}
        self.assertEqual(best['{A}'][1], 100)
        self.assertEqual(best['{Bp}'][1], 10)
        self.assertEqual(best['{C}'][1], 400)
        self.assertEqual(best['{A, Bp}'], ('{Bp} outer, nested_loop_join with A inner', 110))
        self.assertEqual(best['{Bp, C}'], ('{Bp} outer, nested_loop_join with C inner', 410))
        self.assertEqual(best['{A, C}'], ('no predicate, skipped', None))
        self.assertEqual(best['{A, Bp, C}'], ('{A, Bp} outer, nested_loop_join with C inner', 530))

    def test_intermediate_sizes(self):
        planner = Planner(self.tables, self.stats, 32)
        physical = planner.plan(self.logical)
        inner_join = physical['child']['left']
        self.assertEqual((inner_join['est_rows'], inner_join['est_pages']), (1000, 20))


if __name__ == '__main__':
    unittest.main()
