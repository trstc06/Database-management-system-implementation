"""The Section 6 checklist, plus the worked examples in 2.5 and 2.8."""

import unittest
from dbms.common.errors import DbError
from tests.execution.helpers import bag, make_db, random_rows, run, scan

INTS = ['a:INT', 'b:INT', 'c:INT']
ROWS_PER_PAGE = 255   # (INT, INT, INT)


def join(op: str, condition: str = 'R.b = S.b') -> dict:
    return {'op': op, 'condition': condition, 'left': scan('R'), 'right': scan('S')}


class SortTests(unittest.TestCase):
    def test_section_2_5_variant(self):
        # Arrange: N = 8 pages, B = 3. My runs are B - 1 = 2 pages (the input page takes a frame).
        db = make_db({'R': (INTS, random_rows(8 * ROWS_PER_PAGE, 1000, 1))})
        # Act
        result = run(db, {'op': 'sort', 'keys': [['R.b', 'asc']], 'child': scan('R')}, 3)
        # Assert: 4 runs; pass 1 merges 2 + 2; pass 2 streams. Sort's own I/O is 16 reads, 16 writes.
        sort = result.nodes[0][2]
        self.assertEqual((sort['page_reads'], sort['page_writes']), (16, 16))
        self.assertEqual([r[1] for r in result.rows], sorted(r[1] for r in result.rows))

    def test_sort_of_20_b_pages_at_b_4(self):
        db = make_db({'R': (INTS, random_rows(80 * ROWS_PER_PAGE, 5000, 2))})
        result = run(db, {'op': 'sort', 'keys': [['R.b', 'desc'], ['R.a', 'asc']], 'child': scan('R')}, 4)
        keys = [(-r[1], r[0]) for r in result.rows]
        self.assertEqual(keys, sorted(keys))
        self.assertLessEqual(result.peak_frames, 4)


class JoinTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db({'R': (INTS, random_rows(900, 40, 3)), 'S': (INTS, random_rows(700, 40, 4))})

    def test_three_joins_return_the_same_bag(self):
        bags = [bag(run(self.db, join(op), 4).rows)
                for op in ('nested_loop_join', 'sort_merge_join', 'hash_join')]
        self.assertEqual(bags[0], bags[1])
        self.assertEqual(bags[0], bags[2])

    def test_nested_loop_compares_every_pair(self):
        result = run(self.db, join('nested_loop_join', 'R.a < S.a'), 5)
        self.assertEqual(result.nodes[0][2]['comparisons'], 900 * 700)

    def test_output_larger_than_both_inputs(self):
        result = run(self.db, join('hash_join'), 4)
        self.assertGreater(len(result.rows), 900 + 700)

    def test_duplicate_keys_section_2_8(self):
        db = make_db({'R': (['b:INT'], [(1,), (2,), (2,), (3,)]), 'S': (['b:INT'], [(2,), (2,), (4,)])})
        self.assertEqual(len(run(db, join('sort_merge_join'), 4).rows), 4)

    def test_long_duplicate_run_spills(self):
        db = make_db({'R': (INTS, [(i, 7, 0) for i in range(3)]), 'S': (INTS, [(i, 7, 0) for i in range(2000)])})
        self.assertEqual(len(run(db, join('sort_merge_join'), 4).rows), 6000)

    def test_skewed_hash_join_overflows(self):
        rows = [(i, 0 if i % 5 < 2 else i, 0) for i in range(3000)]
        db = make_db({'R': (INTS, rows), 'S': (INTS, rows)})
        result = run(db, join('hash_join'), 4)
        self.assertEqual(len(result.rows), 1200 * 1200 + 1800)

    def test_mixed_types_are_rejected(self):
        db = make_db({'R': (['b:INT'], []), 'S': (['b:VARCHAR(5)'], [])})
        for op in ('nested_loop_join', 'sort_merge_join', 'hash_join'):
            with self.assertRaises(DbError):
                run(db, join(op), 4)


class EmptyAndEdgeTests(unittest.TestCase):
    def setUp(self):
        self.db = make_db({'R': (INTS, []), 'S': (INTS, random_rows(50, 5, 5))})

    def test_every_operator_on_empty_input(self):
        plans = [
            scan('R'),
            {'op': 'filter', 'condition': 'R.a > 1', 'child': scan('R')},
            {'op': 'project', 'columns': ['R.a'], 'child': scan('R')},
            {'op': 'distinct', 'mode': 'sort', 'child': scan('R')},
            {'op': 'distinct', 'mode': 'hash', 'child': scan('R')},
            {'op': 'sort', 'keys': [['R.a', 'asc']], 'child': scan('R')},
            {'op': 'limit', 'n': 3, 'child': scan('R')},
            {'op': 'group_by', 'mode': 'hash', 'keys': ['R.a'], 'aggregates': [['count', '*']], 'child': scan('R')},
            join('nested_loop_join'), join('sort_merge_join'), join('hash_join'),
        ] + [{'op': op, 'left': scan('R'), 'right': scan('S')} for op in ('union_all', 'union', 'intersect', 'except')]
        for plan in plans:
            with self.subTest(plan['op']):
                run(self.db, plan, 4)

    def test_group_by_without_keys_on_empty_input(self):
        for mode in ('sort', 'hash'):
            plan = {'op': 'group_by', 'mode': mode, 'keys': [], 'aggregates': [['count', '*']], 'child': scan('R')}
            self.assertEqual(run(self.db, plan, 4).rows, [(0,)])
            plan['aggregates'].append(['avg', 'R.b'])
            with self.assertRaises(DbError):
                run(self.db, plan, 4)

    def test_group_by_modes_agree(self):
        aggregates = [['count', '*'], ['sum', 'S.c'], ['avg', 'S.c'], ['min', 'S.a'], ['max', 'S.a']]
        results = [bag(run(self.db, {'op': 'group_by', 'mode': mode, 'keys': ['S.b'], 'aggregates': aggregates,
                                     'child': scan('S')}, 4).rows) for mode in ('sort', 'hash')]
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(results[0]), 5)

    def test_set_operators(self):
        db = make_db({'R': (['x:INT'], [(1,), (1,), (2,), (3,)]), 'S': (['y:INT'], [(2,), (3,), (3,), (4,)])})
        plan = lambda op: {'op': op, 'left': scan('R'), 'right': scan('S')}
        self.assertEqual(sorted(run(db, plan('union'), 4).rows), [(1,), (2,), (3,), (4,)])
        self.assertEqual(len(run(db, plan('union_all'), 4).rows), 8)
        self.assertEqual(run(db, plan('intersect'), 4).rows, [(2,), (3,)])
        self.assertEqual(run(db, plan('except'), 4).rows, [(1,)])

    def test_limit_stops_pulling(self):
        result = run(self.db, {'op': 'limit', 'n': 3, 'child': scan('S')}, 4)
        self.assertEqual(len(result.rows), 3)
        self.assertEqual(result.nodes[1][2]['tuples_out'], 3)

    def test_bad_plan_names_the_node(self):
        with self.assertRaises(DbError) as caught:
            run(self.db, {'op': 'filter', 'child': scan('R')}, 4)
        self.assertIn("node 'filter' at root", caught.exception.message)
        with self.assertRaises(DbError) as caught:
            run(self.db, {'op': 'project', 'columns': ['R.zz'], 'child': scan('R')}, 4)


class LargeHashTests(unittest.TestCase):
    def test_hash_distinct_and_group_by_partition_at_b_4(self):
        db = make_db({'R': (INTS, random_rows(20 * ROWS_PER_PAGE, 3000, 6))})
        distinct = run(db, {'op': 'distinct', 'mode': 'hash',
                            'child': {'op': 'project', 'columns': ['R.b'], 'child': scan('R')}}, 4)
        expected = {(r[1],) for r in random_rows(20 * ROWS_PER_PAGE, 3000, 6)}
        self.assertEqual(set(distinct.rows), expected)
        self.assertEqual(len(distinct.rows), len(expected))
        grouped = run(db, {'op': 'group_by', 'mode': 'hash', 'keys': ['R.b'], 'aggregates': [['count', '*']],
                           'child': scan('R')}, 4)
        self.assertEqual(sum(r[1] for r in grouped.rows), 20 * ROWS_PER_PAGE)


if __name__ == '__main__':
    unittest.main()
