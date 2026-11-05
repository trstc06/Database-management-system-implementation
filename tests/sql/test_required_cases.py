"""The fifteen required cases in Section 7, numbered as in the handout."""

import unittest
from tests.sql.helpers import columns_in, failure, ops, plan, scan
from tests.sql.test_examples import PHYSICAL


class RequiredCases(unittest.TestCase):
    def test_01_self_join_has_two_renames_over_one_table(self):
        join = plan('SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid;')['child']
        self.assertEqual((join['left'], join['right']), (scan('Emp', 'e'), scan('Emp', 'm')))

    def test_02_star_over_three_tables_with_a_shared_name(self):
        result = plan('SELECT * FROM Emp, Dept, Proj;')
        self.assertEqual(result['columns'][:2], ['Emp.eid', 'Emp.name'])
        for shared in ('Emp.did', 'Dept.did', 'Proj.did'):
            self.assertIn(shared, result['columns'])
        self.assertEqual(len(result['columns']), 7 + 4 + 3)

    def test_03_ambiguous_column_names_both_candidates(self):
        error = failure('SELECT did FROM Emp, Dept;')
        self.assertEqual(error.category, 'ambiguity')
        self.assertIn('Emp.did', error.message)
        self.assertIn('Dept.did', error.message)

    def test_04_union_and_union_all_differ(self):
        union = plan('SELECT city FROM Emp UNION SELECT city FROM Emp;')
        union_all = plan('SELECT city FROM Emp UNION ALL SELECT city FROM Emp;')
        self.assertEqual((union['op'], union_all['op']), ('union', 'union_all'))
        self.assertEqual(union['left'], union_all['left'])

    def test_05_where_below_and_having_above_the_grouping(self):
        result = plan('SELECT did, SUM(salary) FROM Emp WHERE age > 25 GROUP BY did HAVING SUM(salary) > 100;')
        self.assertEqual(ops(result), ['project', 'select', 'group_by', 'select', 'scan'])
        self.assertEqual(result['child']['condition'], 'sum_Emp_salary > 100')
        self.assertEqual(result['child']['child']['child']['condition'], 'Emp.age > 25')

    def test_06_non_grouped_column_in_select_list(self):
        self.assertEqual(failure('SELECT name, COUNT(*) FROM Emp GROUP BY did;').category, 'aggregate')

    def test_07_aggregate_in_where(self):
        self.assertEqual(failure('SELECT name FROM Emp WHERE COUNT(*) > 2;').category, 'aggregate')

    def test_08_subquery_in_from_joined_to_a_base_table(self):
        sql = ('SELECT d.title, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t '
               'JOIN Dept d ON t.did = d.did;')
        join = plan(sql)['child']
        self.assertEqual(join['op'], 'join')
        self.assertEqual(join['condition'], 't.did = d.did')
        self.assertEqual(join['left']['op'], 'rename')
        self.assertEqual(join['left']['child']['child']['op'], 'group_by')

    def test_09_order_by_a_column_not_selected(self):
        result = plan('SELECT name FROM Emp ORDER BY age DESC;')
        self.assertEqual(ops(result), ['project', 'sort', 'project', 'scan'])
        self.assertEqual(result['child']['keys'], [['Emp.age', 'desc']])
        self.assertEqual(result['columns'], ['Emp.name'])

    def test_10_string_compared_with_int(self):
        error = failure("SELECT name FROM Emp WHERE age > '30';")
        self.assertEqual((error.category, error.line, error.column), ('type', 1, 28))

    def test_11_unknown_table_and_unknown_column(self):
        table, column = failure('SELECT a FROM Nope;'), failure('SELECT nope FROM Emp;')
        self.assertEqual((table.category, column.category), ('name', 'name'))
        self.assertIn("table 'Nope'", table.message)
        self.assertIn("column 'nope'", column.message)
        self.assertIn("'Emp' has no column 'x'", failure('SELECT Emp.x FROM Emp;').message)

    def test_12_same_table_twice_without_aliases(self):
        error = failure('SELECT * FROM Emp, Emp;')
        self.assertEqual(error.category, 'name')
        self.assertIn('twice', error.message)

    def test_13_count_star_without_group_by(self):
        result = plan('SELECT COUNT(*) FROM Emp;')
        self.assertEqual(result['child'], {'op': 'group_by', 'keys': [],
                                           'aggregates': [['count', '*', 'count_star']], 'child': scan('Emp')})

    def test_14_union_with_mismatched_types(self):
        self.assertEqual(failure('SELECT name FROM Emp UNION SELECT budget FROM Dept;').category, 'schema')
        self.assertEqual(failure('SELECT name, age FROM Emp UNION SELECT title FROM Dept;').category, 'schema')

    def test_15_four_table_chain_commas_and_join_on(self):
        commas = plan('SELECT e.name FROM Emp e, Dept d, Proj p, Site s '
                      'WHERE e.did = d.did AND d.did = p.did AND p.pid = s.pid;')
        joins = plan('SELECT e.name FROM Emp e JOIN Dept d ON e.did = d.did '
                     'JOIN Proj p ON d.did = p.did JOIN Site s ON p.pid = s.pid;')
        self.assertEqual(ops(commas), ['project', 'select', 'cross', 'cross', 'cross'] + ['rename', 'scan'] * 4)
        self.assertEqual(ops(joins), ['project', 'join', 'join', 'join'] + ['rename', 'scan'] * 4)
        self.assertEqual(commas['child']['condition'], 'e.did = d.did and d.did = p.did and p.pid = s.pid')
        self.assertEqual(joins['child']['condition'], 'p.pid = s.pid')


class PlanRules(unittest.TestCase):
    QUERIES = [
        'SELECT name, age FROM Emp WHERE age > 30;',
        'SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did;',
        'SELECT DISTINCT city FROM Emp ORDER BY city;',
        'SELECT did, MAX(name) FROM Emp GROUP BY did ORDER BY did;',
        'SELECT city FROM Emp INTERSECT SELECT city FROM Dept EXCEPT SELECT city FROM Site;',
    ]

    def test_no_physical_names_and_no_unqualified_columns(self):
        for sql in self.QUERIES:
            with self.subTest(sql):
                result = plan(sql)
                self.assertFalse(PHYSICAL & set(ops(result)))
                for name in columns_in(result):
                    self.assertTrue('.' in name or name.startswith(('count_', 'max_')), name)

    def test_set_operators_group_to_the_left(self):
        result = plan(self.QUERIES[4])
        self.assertEqual((result['op'], result['left']['op']), ('except', 'intersect'))

    def test_distinct_only_where_written(self):
        self.assertNotIn('distinct', ops(plan('SELECT city FROM Emp;')))
        self.assertEqual(ops(plan('SELECT DISTINCT city FROM Emp;'))[:2], ['distinct', 'project'])
        self.assertNotIn('distinct', ops(plan('SELECT DISTINCT did, COUNT(*) FROM Emp GROUP BY did;')))
        # Without every grouping column the counts can repeat, so DISTINCT is still needed.
        self.assertIn('distinct', ops(plan('SELECT DISTINCT COUNT(*) FROM Emp GROUP BY did;')))

    def test_column_alias_and_order_by_alias(self):
        result = plan('SELECT name AS who FROM Emp ORDER BY who;')
        self.assertEqual(result['keys'], [['who', 'asc']])
        self.assertEqual(result['child']['aliases'], ['who'])

    def test_more_name_errors(self):
        self.assertEqual(failure('SELECT name, name FROM Emp;').category, 'schema')
        self.assertEqual(failure('SELECT SUM(name) FROM Emp;').category, 'type')
        self.assertEqual(failure('SELECT DISTINCT name FROM Emp ORDER BY age;').category, 'name')
        self.assertEqual(failure('SELECT e.name FROM Emp e JOIN Dept d ON e.did = x.did;').category, 'name')
        self.assertEqual(failure('SELECT did FROM Emp GROUP BY did HAVING age > 3;').category, 'aggregate')


if __name__ == '__main__':
    unittest.main()
