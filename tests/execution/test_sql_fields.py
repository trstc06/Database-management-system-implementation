"""Fields Module 3 plans carry that the executor must accept."""

import unittest
from tests.execution.helpers import make_db, run, scan


class SqlFieldTests(unittest.TestCase):
    def setUp(self):
        rows = [(i, i % 3, i * 10) for i in range(12)]
        self.db = make_db({'Emp': (['eid:INT', 'did:INT', 'salary:INT'], rows)})

    def test_named_aggregates_and_having_on_avg(self):
        grouped = {'op': 'group_by', 'mode': 'hash', 'keys': ['Emp.did'],
                   'aggregates': [['count', '*', 'n'], ['avg', 'Emp.salary', 'avg_sal']], 'child': scan('Emp')}
        plan = {'op': 'project', 'columns': ['Emp.did', 'n', 'avg_sal'],
                'child': {'op': 'filter', 'condition': 'avg_sal > 55', 'child': grouped}}
        result = run(self.db, plan, 4)
        self.assertEqual(result.schema(), [['Emp.did', 'INT'], ['n', 'INT'], ['avg_sal', 'REAL']])
        # Averages are 45, 55 and 65 for did 0, 1 and 2; only did 2 is over 55.
        self.assertEqual(result.rows, [(2, 4, 65.0)])

    def test_project_aliases_rename_columns(self):
        plan = {'op': 'project', 'columns': ['Emp.eid', 'Emp.did'], 'aliases': ['id', None], 'child': scan('Emp')}
        result = run(self.db, plan, 4)
        self.assertEqual([name for name, *_ in result.schema()], ['id', 'Emp.did'])


if __name__ == '__main__':
    unittest.main()
