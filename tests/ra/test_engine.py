"""Execution checks for cases 18-25 and earlier nested queries."""

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import mock_open, patch
from dbms.common.errors import ExecutionError, LexicalError
from dbms.ra.cli import main
from dbms.ra.evaluate import execute, format_relation
from dbms.ra.relfile import load_relations


EMPLOYEES = '''Employees(EID,Name,Age,DID)={
E1,John,32,D1
E2,Alice,28,D2
E3,Bob,29,D1
}
'''
JOINS = '''Emp(EID,MgrID,DID)={
E1,E1,D1
E2,E1,D2
E3,E2,D1
}
Dept(DID,Title)={
D1,Sales
D2,Support
}
'''


class EngineTests(unittest.TestCase):
    def run_query(self, query, source=EMPLOYEES):
        return execute(query, load_relations(source))

    def assert_error(self, category, query, source=EMPLOYEES, message=''):
        with self.assertRaises(ExecutionError) as result:
            self.run_query(query, source)
        self.assertIn(category + ' error:', str(result.exception))
        self.assertIn(message, str(result.exception))

    def test_14_nested_execution(self):
        result = self.run_query("project[Name](select[Age>30](select[DID='D1'](Employees)))")
        self.assertEqual(result.rows, (('John',),))
        self.assertEqual([c.label for c in result.columns], ['Name'])

    def test_18_column_to_column(self):
        result = self.run_query('select[A=B](R)', 'R(A,B)={\n1,1\n1,2\n2,2\n}\n')
        self.assertEqual(result.rows, ((1, 1), (2, 2)))

    def test_19_theta_join(self):
        result = self.run_query('Emp join[Emp.DID=Dept.DID] Dept', JOINS)
        self.assertEqual([c.label for c in result.columns],
                         ['Emp.EID', 'Emp.MgrID', 'Emp.DID', 'Dept.DID', 'Dept.Title'])
        self.assertEqual(result.rows, (('E1', 'E1', 'D1', 'D1', 'Sales'),
                                     ('E2', 'E1', 'D2', 'D2', 'Support'),
                                     ('E3', 'E2', 'D1', 'D1', 'Sales')))
        self.assertEqual(result, self.run_query('select[Emp.DID=Dept.DID](Emp times Dept)', JOINS))

    def test_20_rename_self_join(self):
        result = self.run_query('rename[E2](Emp) join[Emp.MgrID=E2.EID] Emp', JOINS)
        self.assertEqual([c.label for c in result.columns],
                         ['E2.EID', 'E2.MgrID', 'E2.DID', 'Emp.EID', 'Emp.MgrID', 'Emp.DID'])
        self.assertEqual(result.rows, (('E1', 'E1', 'D1', 'E1', 'E1', 'D1'),
                                     ('E1', 'E1', 'D1', 'E2', 'E1', 'D2'),
                                     ('E2', 'E1', 'D2', 'E3', 'E2', 'D1')))
        self.assert_error('Schema', 'Emp times Emp', JOINS, 'duplicate column')

    def test_21_incompatible_schemas(self):
        sources = ['R(a)={\n1\n}\nS(b)={\n1\n}',
                   'R(a)={\n1\n}\nS(a,b)={\n1,2\n}',
                   'R(a,b)={\n1,2\n}\nS(b,a)={\n1,2\n}',
                   "R(a)={\n1\n}\nS(a)={\n'1'\n}"]
        for source in sources:
            for operation in ['union', 'intersect', 'minus']:
                with self.subTest(source=source, operation=operation):
                    self.assert_error('Schema', f'R {operation} S', source)

    def test_22_number_string_comparison(self):
        self.assert_error('Type', "select[Age>'30'](R)", 'R(Age)={\n32\n}')

    def test_23_projection_keeps_duplicates_distinct_removes_them(self):
        # Changed meaning under bag semantics (refactor point 6): projection no
        # longer removes duplicates; the separate distinct operator does.
        self.assertEqual(self.run_query('project[DID](Employees)').rows, (('D1',), ('D2',), ('D1',)))
        self.assertEqual(self.run_query('distinct(project[DID](Employees))').rows, (('D1',), ('D2',)))

    def test_24_repeated_projection_is_error(self):
        for query in ['project[Name,Name](R)', 'project[Name,R.Name](R)']:
            self.assert_error('Schema', query, 'R(Name)={\nJohn\n}', 'repeats column')

    def test_25_empty_result_keeps_schema(self):
        result = self.run_query('select[Age>100](Employees)')
        original = load_relations(EMPLOYEES)['Employees']
        self.assertEqual(result.columns, original.columns)
        self.assertEqual(result.rows, ())
        self.assertEqual(format_relation(result), '(EID, Name, Age, DID) = {\n}')

    def test_set_operations_and_left_schema(self):
        source = 'R(x)={\n1\n2\n2\n}\nS(x)={\n2\n3\n}'
        expected = {'union': ((1,), (2,), (3,)), 'intersect': ((2,),), 'minus': ((1,),)}
        for op, rows in expected.items():
            with self.subTest(op=op):
                result = self.run_query(f'R {op} S', source)
                self.assertEqual(result.rows, rows)
                self.assertEqual(result.columns, load_relations(source)['R'].columns)

    def test_11_associativity_changes_execution(self):
        source = 'A(x)={\n1\n2\n}\nB(x)={\n2\n}\nC(x)={\n2\n}'
        self.assertEqual(self.run_query('A minus B minus C', source).rows, ((1,),))
        self.assertEqual(self.run_query('A minus (B minus C)', source).rows, ((1,), (2,)))

    def test_10_grammar_ambiguity_example(self):
        source = 'A(x)={\n1\n2\n}\nB(x)={\n2\n}\nC(x)={\n2\n}'
        self.assertEqual(self.run_query('A union B minus C', source).rows, ((1,),))
        self.assertEqual(self.run_query('(A union B) minus C', source).rows, ((1,),))
        self.assertEqual(self.run_query('A union (B minus C)', source).rows, ((1,), (2,)))

    def test_conditions_and_all_comparisons(self):
        source = 'R(a,b,c)={\n1,2,0\n0,2,3\n0,0,4\n1,0,0\n}'
        self.assertEqual(self.run_query('select[not (a=1 and b=2) or c>3](R)', source).rows,
                         ((0, 2, 3), (0, 0, 4), (1, 0, 0)))
        self.assertEqual(self.run_query('select[a=1 and b=2 or c=3](R)', source).rows,
                         ((1, 2, 0), (0, 2, 3)))
        for op, values in [('=', (2,)), ('!=', (1, 3)), ('<', (1,)),
                           ('<=', (1, 2)), ('>', (3,)), ('>=', (2, 3))]:
            with self.subTest(op=op):
                result = self.run_query(f'select[x{op}2](R)', 'R(x)={\n1\n2\n3\n}')
                self.assertEqual(result.rows, tuple((v,) for v in values))

    def test_projection_order_and_qualifiers(self):
        result = self.run_query('project[Employees.DID,Name](Employees)')
        self.assertEqual([c.label for c in result.columns], ['DID', 'Name'])
        self.assertEqual(result.rows, (('D1', 'John'), ('D2', 'Alice'), ('D1', 'Bob')))
        joined = self.run_query('project[Dept.DID,Emp.DID](Emp times Dept)', JOINS)
        self.assertEqual([c.label for c in joined.columns], ['Dept.DID', 'Emp.DID'])
        # Bag semantics: all 3 x 2 pairs stay; distinct gives Bonus 1's 4 rows.
        self.assertEqual(len(joined.rows), 6)
        self.assertEqual(len(self.run_query('distinct(project[Dept.DID,Emp.DID](Emp times Dept))', JOINS).rows), 4)

    def test_product_is_full_nested_loop_and_keeps_lineage(self):
        source = 'R(a)={\n1\n2\n}\nS(b)={\n3\n4\n}\nT(c)={\n5\n}'
        result = self.run_query('R times S times T', source)
        self.assertEqual(result.rows, ((1, 3, 5), (1, 4, 5), (2, 3, 5), (2, 4, 5)))
        self.assertEqual([c.label for c in result.columns], ['R.a', 'S.b', 'T.c'])
        self.assert_error('Schema', 'R times S times R', source)

    def test_rename_changes_qualifier_without_changing_source(self):
        tables = load_relations(EMPLOYEES)
        original = tables['Employees']
        result = execute('project[E.Name](rename[E](Employees))', tables)
        self.assertEqual(result.columns[0].qualifier, 'E')
        self.assertEqual(tables['Employees'], original)
        self.assert_error('Name', 'project[Employees.Name](rename[E](Employees))')
        self.assert_error('Schema', 'rename[X](Emp times Dept)', JOINS)

    def test_names_checked_even_on_empty_inputs(self):
        self.assert_error('Name', 'Unknown')
        self.assert_error('Name', 'project[Missing](Employees)')
        self.assert_error('Name', 'select[DID=1](Emp times Dept)', JOINS, 'ambiguous')
        self.assert_error('Name', 'select[Missing=1](select[Age>100](Employees))')
        self.assert_error('Name', 'select[1=1 or Missing=1](Employees)')

    def test_type_errors_not_hidden_by_empty_or_short_circuit(self):
        self.assert_error('Type', "select[Age>'30'](select[Age>100](Employees))")
        self.assert_error('Type', "select[1=1 or Age='30'](Employees)")
        self.assert_error('Type', 'select[Age=Name](Employees)')

    def test_loader_validates_rows_and_schema(self):
        for source, category in [('R(a,b)={\n1\n}', 'Schema'),
                                 ('R(a)={\n1,2\n}', 'Schema'),
                                 ('R(a,a)={\n1,2\n}', 'Schema'),
                                 ('R(a)={\n1\n}\nR(a)={\n2\n}', 'Schema'),
                                 ("R(a)={\n1\n'1'\n}", 'Type')]:
            with self.subTest(source=source):
                self.assert_error(category, 'R', source)

    def test_integer_and_string_values_in_a_bag(self):
        # Changed for Project 2: INT only (no decimals) and tables are bags.
        result = self.run_query('R', "R(n,s)={\n1,'O''Brien'\n1,'O''Brien'\n-25,'a,b)'\n}")
        self.assertEqual(result.rows, ((1, "O'Brien"), (1, "O'Brien"), (-25, 'a,b)')))
        self.assertEqual(format_relation(result), "(n, s) = {\n  1, 'O''Brien'\n  1, 'O''Brien'\n  -25, 'a,b)'\n}")
        with self.assertRaises(LexicalError):
            self.run_query('R', 'R(n)={\n1.00\n}')
        result = self.run_query("select[s<'Z'](R)", "R(s)={\nAlice\nZoe\n}")
        self.assertEqual(result.rows, (('Alice',),))

    def test_empty_definition_types_and_empty_operators(self):
        source = 'R(x)={\n}\nS(x)={\n1\n}\nT(y)={\n2\n}'
        result = self.run_query('R union S', source)
        self.assertEqual(result.rows, ((1,),))
        self.assertEqual(result.columns[0].type, 'INT')
        for query in ['R intersect S', 'R minus S', 'S minus S',
                      'project[x](R)', 'rename[E](R)', 'R times T', 'R join[x=y] T']:
            with self.subTest(query=query):
                result = self.run_query(query, source)
                self.assertEqual(result.rows, ())
                self.assertTrue(result.columns)
        self.assert_error('Schema', 'R union T', source)


class ExecutionCommandTests(unittest.TestCase):
    def command(self, query):
        output, error = StringIO(), StringIO()
        with patch('sys.argv', ['ra', '--relations', 'tables.txt', '--query', query]), \
                patch('builtins.open', mock_open(read_data=EMPLOYEES)), \
                redirect_stdout(output), redirect_stderr(error):
            code = main()
        return code, output.getvalue(), error.getvalue()

    def test_cli_result_and_empty_body(self):
        self.assertEqual(self.command('project[DID](Employees)'),
                         (0, "(DID) = {\n  'D1'\n  'D2'\n  'D1'\n}\n", ''))
        self.assertEqual(self.command('select[Age>100](Employees)'),
                         (0, '(EID, Name, Age, DID) = {\n}\n', ''))

    def test_semantic_errors_print_without_tracebacks(self):
        for query, category in [('Missing', 'Name'), ('Employees times Employees', 'Schema'),
                                ("select[Age>'30'](Employees)", 'Type')]:
            with self.subTest(query=query):
                code, output, error = self.command(query)
                self.assertEqual(code, 1)
                self.assertEqual(output, '')
                self.assertIn(category + ' error:', error)
                self.assertNotIn('Traceback', error)

    def test_cli_file_error(self):
        output = StringIO()
        with patch('sys.argv', ['ra', '--relations', 'missing.txt', '--query', 'R']), \
                patch('builtins.open', side_effect=OSError('file not found')), redirect_stderr(output):
            self.assertEqual(main(), 1)
        self.assertIn('file not found', output.getvalue())


if __name__ == '__main__':
    unittest.main()
