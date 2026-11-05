"""Every worked example in Section 5, compared as whole JSON plans."""

import unittest
from tests.sql.helpers import plan, scan

PHYSICAL = {'seqscan', 'filter', 'nested_loop_join', 'sort_merge_join', 'hash_join'}


class SectionFiveExamples(unittest.TestCase):
    def test_5_1_filter_and_projection(self):
        self.assertEqual(plan('SELECT name, age FROM Emp WHERE age > 30;'), {
            'op': 'project', 'columns': ['Emp.name', 'Emp.age'],
            'child': {'op': 'select', 'condition': 'Emp.age > 30', 'child': scan('Emp')}})

    def test_5_2_join_with_aliases(self):
        sql = 'SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did WHERE d.budget > 1000;'
        self.assertEqual(plan(sql), {
            'op': 'project', 'columns': ['e.name', 'd.title'],
            'child': {'op': 'select', 'condition': 'd.budget > 1000',
                      'child': {'op': 'join', 'condition': 'e.did = d.did',
                                'left': scan('Emp', 'e'), 'right': scan('Dept', 'd')}}})

    def test_5_3_comma_join(self):
        result = plan('SELECT * FROM Emp e, Dept d WHERE e.did = d.did;')
        self.assertEqual(result['child'], {
            'op': 'select', 'condition': 'e.did = d.did',
            'child': {'op': 'cross', 'left': scan('Emp', 'e'), 'right': scan('Dept', 'd')}})

    def test_5_4_grouping_with_having(self):
        sql = ('SELECT did, COUNT(*) AS n, AVG(salary) AS avg_sal FROM Emp WHERE age > 25 '
               'GROUP BY did HAVING COUNT(*) > 5 ORDER BY n DESC LIMIT 10;')
        self.assertEqual(plan(sql), {
            'op': 'limit', 'n': 10, 'child': {
                'op': 'sort', 'keys': [['n', 'desc']], 'child': {
                    'op': 'project', 'columns': ['Emp.did', 'n', 'avg_sal'], 'child': {
                        'op': 'select', 'condition': 'n > 5', 'child': {
                            'op': 'group_by', 'keys': ['Emp.did'],
                            'aggregates': [['count', '*', 'n'], ['avg', 'Emp.salary', 'avg_sal']],
                            'child': {'op': 'select', 'condition': 'Emp.age > 25', 'child': scan('Emp')}}}}}})

    def test_5_5_self_join(self):
        result = plan('SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid;')
        self.assertEqual(result['child'], {'op': 'join', 'condition': 'e.mgr = m.eid',
                                           'left': scan('Emp', 'e'), 'right': scan('Emp', 'm')})

    def test_5_6_subquery_in_from(self):
        sql = 'SELECT t.did, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t WHERE t.n > 3;'
        self.assertEqual(plan(sql), {
            'op': 'project', 'columns': ['t.did', 't.n'],
            'child': {'op': 'select', 'condition': 't.n > 3', 'child': {
                'op': 'rename', 'to': 't', 'child': {
                    'op': 'project', 'columns': ['Emp.did', 'n'], 'child': {
                        'op': 'group_by', 'keys': ['Emp.did'], 'aggregates': [['count', '*', 'n']],
                        'child': scan('Emp')}}}}})

    def test_5_7_the_two_unions(self):
        branches = lambda: [{'op': 'project', 'columns': ['Emp.city'], 'child': scan('Emp')},
                            {'op': 'project', 'columns': ['Dept.city'], 'child': scan('Dept')}]
        for keyword, op in (('UNION ALL', 'union_all'), ('UNION', 'union')):
            left, right = branches()
            self.assertEqual(plan(f'SELECT city FROM Emp {keyword} SELECT city FROM Dept;'),
                             {'op': op, 'left': left, 'right': right})


if __name__ == '__main__':
    unittest.main()
