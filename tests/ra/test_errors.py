"""Day 10: check errors through the real command-line program."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DATA = 'R(Age,Name)={\n32,Bob\n}\nS(Age)={\n32\n}\n'


class ErrorCommandTests(unittest.TestCase):
    def run_command(self, *args):
        return subprocess.run([sys.executable, '-m', 'dbms.ra.cli', *args],
                              capture_output=True, text=True, timeout=10, cwd=ROOT,
                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(ROOT)})

    def query(self, query, data=DATA):
        with tempfile.NamedTemporaryFile(suffix='.txt') as source:
            source.write(data.encode('utf-8'))
            source.flush()
            return self.run_command('--relations', source.name, '--query', query)

    def assert_error(self, result, category, *details):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn(category + ' error', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        for detail in details:
            self.assertIn(detail, result.stderr)

    def test_lexical_errors_in_both_query_modes(self):
        cases = [("select[Name='Bob](R)", 'line 1, column 13', 'unterminated string'),
                 ('R\n@', 'line 2, column 1', 'unexpected character')]
        for query, position, problem in cases:
            for tree_mode in (False, True):
                with self.subTest(query=query, tree=tree_mode):
                    result = self.run_command('--tree', query) if tree_mode else self.query(query)
                    self.assert_error(result, 'Lexical', position, problem)

    def test_syntax_errors_in_both_query_modes(self):
        cases = [('select[Age>30](R', 'line 1, column 17', "closing ')'"),
                 ('project[](R)', 'line 1, column 9', 'list cannot be empty'),
                 ('R union\n', 'line 2, column 1', 'end of input'),
                 ('R)', 'line 1, column 2', 'expected end of input')]
        for query, position, problem in cases:
            for tree_mode in (False, True):
                with self.subTest(query=query, tree=tree_mode):
                    result = self.run_command('--tree', query) if tree_mode else self.query(query)
                    self.assert_error(result, 'Syntax', position, problem)

    def test_name_errors(self):
        for query, message in [('Missing', 'unknown relation Missing'),
                               ('project[Missing](R)', 'unknown attribute Missing'),
                               ('select[Age=32](R times S)', 'ambiguous attribute Age'),
                               ('project[R.Age](rename[X](R))', 'unknown attribute R.Age')]:
            with self.subTest(query=query):
                self.assert_error(self.query(query), 'Name', message)

    def test_schema_errors(self):
        for query, message in [('R union S', 'same number of columns'),
                               ('R times R', 'duplicate column R.Age'),
                               ('project[Name,R.Name](R)', 'repeats column')]:
            with self.subTest(query=query):
                self.assert_error(self.query(query), 'Schema', message)
        self.assert_error(self.query('R union T', DATA + 'T(Name,Age)={\nBob,32\n}'),
                          'Schema', 'column 1', 'Age', 'Name')

    def test_type_and_name_checks_on_empty_and_skipped_branches(self):
        for query in ["select[Age>'30'](R)", "select[Age=Name](R)",
                      "select[Age='30'](select[Age>100](R))",
                      "select[1=1 or Age='30'](R)"]:
            with self.subTest(query=query):
                self.assert_error(self.query(query), 'Type', 'number', 'string')
        self.assert_error(self.query('select[1=1 or Missing=1](R)'), 'Name', 'Missing')
        self.assert_error(self.query('select[Missing=1](select[Age>100](R))'), 'Name', 'Missing')

    def test_relation_file_errors(self):
        cases = [("R(x)={\r\n'bad\r\n}", 'Lexical', ('line 2, column 1', 'unterminated')),
                 ('R(x)={\n1\n', 'Syntax', ('line 3, column 1', "closing '}'")),
                 ('R(x,y)={\n1\n}', 'Schema', ('line 2', 'expected 2 values, got 1')),
                 ('R(x,x)={\n1,2\n}', 'Schema', ('duplicate column x',)),
                 ('R(x)={\n}\nR(x)={\n}', 'Schema', ('duplicate relation R',)),
                 ("R(x)={\n1\n'1'\n}", 'Type', ('R.x', 'line 3'))]
        for data, category, details in cases:
            with self.subTest(data=data):
                self.assert_error(self.query('R', data), category, *details)

    def test_excessive_parser_and_execution_depth(self):
        self.assert_error(self.run_command('--tree', '(' * 1500 + 'R' + ')' * 1500),
                          'Syntax', 'line 1, column', 'too deep')
        # Flat syntax parses with a loop but builds a deep execution tree.
        self.assert_error(self.query(' union '.join(['R'] * 1500)),
                          'Syntax', 'line 1, column', 'too deep to execute')
        condition = ' or '.join(['Age=0'] * 1500)
        self.assert_error(self.query(f'select[{condition}](R)'),
                          'Syntax', 'line 1, column', 'too deep to execute')

    def test_file_and_usage_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_command('--relations', str(Path(folder) / 'missing.txt'), '--query', 'R')
            self.assertEqual(result.returncode, 1)
            self.assertIn('missing.txt', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        with tempfile.NamedTemporaryFile() as source:
            source.write(b'\xff')
            source.flush()
            result = self.run_command('--relations', source.name, '--query', 'R')
            self.assertEqual(result.returncode, 1)
            self.assertIn('decode', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        result = self.run_command('--query', 'R')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--query requires --relations FILE', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_valid_empty_result_is_not_an_error(self):
        result = self.query('select[Age>100](R)')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(result.stdout, '(Age, Name) = {\n}\n')


if __name__ == '__main__':
    unittest.main()
