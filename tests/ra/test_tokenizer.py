"""Lexical checks for assignment cases 1-9, plus scanner edge cases."""

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch
from dbms.common.errors import LexicalError
from dbms.ra.cli import main
from dbms.ra.tokenizer import tokenize


def signature(source):
    return [(token.kind, token.value) for token in tokenize(source)]


class TokenizerTests(unittest.TestCase):
    def test_01_no_whitespace(self):
        self.assertEqual(signature('select[x1=3](R)'), [
            ('NAME', 'select'), ('LBRACKET', '['), ('NAME', 'x1'),
            ('COMPARE', '='), ('NUMBER', '3'), ('RBRACKET', ']'),
            ('LPAREN', '('), ('NAME', 'R'), ('RPAREN', ')'), ('EOF', ''),
        ])

    def test_02_whitespace_has_same_token_meanings(self):
        self.assertEqual(signature('select[ x1 = 3 ](R)'), signature('select[x1=3](R)'))

    def test_03_maximal_munch(self):
        self.assertEqual(signature('select[Age>=30](R)')[3:5], [('COMPARE', '>='), ('NUMBER', '30')])

    def test_04_negative_number(self):
        self.assertEqual(signature('select[Age>-30](R)')[3:5], [('COMPARE', '>'), ('NUMBER', '-30')])

    def test_05_parenthesis_in_string(self):
        self.assertEqual(signature("select[Name='Bob)'](R)")[4], ('STRING', 'Bob)'))

    def test_06_comma_in_string(self):
        self.assertEqual(signature("select[Name='a,b'](R)")[4], ('STRING', 'a,b'))

    def test_07_doubled_quote(self):
        token = tokenize("select[Name='O''Brien'](R)")[4]
        self.assertEqual(token.value, "O'Brien")
        self.assertEqual(token.text, "'O''Brien'")

    def test_08_keyword_attribute(self):
        self.assertEqual(signature('select[union=3](R)')[2], ('NAME', 'union'))

    def test_09_unterminated_string(self):
        with self.assertRaises(LexicalError) as result:
            tokenize("select[Name='Bob](R)")
        self.assertEqual((result.exception.offset, result.exception.line, result.exception.column), (12, 1, 13))
        self.assertIn('unterminated string', str(result.exception))

    def test_comparisons_and_qualified_names(self):
        self.assertEqual(signature('Emp.DID!=Dept.DID')[:4], [
            ('NAME', 'Emp'), ('DOT', '.'), ('NAME', 'DID'), ('COMPARE', '!='),
        ])
        self.assertEqual([t.value for t in tokenize('= != <> < <= > >=')[:-1]], ['=', '!=', '<>', '<', '<=', '>', '>='])

    def test_empty_string_and_quote_value(self):
        self.assertEqual(signature("'' ''''"), [('STRING', ''), ('STRING', "'"), ('EOF', '')])

    def test_positions_comments_and_windows_newlines(self):
        source = '  // comment\r\n select[x=1](R)'
        tokens = tokenize(source)
        self.assertEqual((tokens[0].offset, tokens[0].line, tokens[0].column), (15, 2, 2))
        for token in tokens:
            self.assertEqual(source[token.offset:token.offset + len(token.text)], token.text)
        self.assertEqual(tokens[-1].offset, len(source))

    def test_whole_names_and_integers_only(self):
        # Changed for Project 2: INT is the only numeric type, so -30.5 is rejected.
        self.assertEqual(signature('unionize x1 -30'), [
            ('NAME', 'unionize'), ('NAME', 'x1'), ('NUMBER', '-30'), ('EOF', ''),
        ])
        with self.assertRaises(LexicalError) as result:
            tokenize('x > -30.5')
        self.assertIn('only integer numbers', str(result.exception))

    def test_invalid_characters(self):
        for source in ('@', '!', '-', '/', '+3'):
            with self.subTest(source=source), self.assertRaises(LexicalError):
                tokenize(source)

    def test_newline_inside_string(self):
        with self.assertRaises(LexicalError):
            tokenize("'Bob\n'")


class RelationTokenizerTests(unittest.TestCase):
    def test_assignment_table(self):
        source = ('// employees and their departments\n'
                  'Employees (EID, Name, Age, DID) = {\n'
                  ' E1, John, 32, D1\n E2, Alice, 28, D2\n E3, Bob, 29, D1\n}\n')
        tokens = tokenize(source, relations=True)
        self.assertEqual([(t.kind, t.value) for t in tokens[:13]], [
            ('NAME', 'Employees'), ('LPAREN', '('), ('NAME', 'EID'),
            ('COMMA', ','), ('NAME', 'Name'), ('COMMA', ','),
            ('NAME', 'Age'), ('COMMA', ','), ('NAME', 'DID'),
            ('RPAREN', ')'), ('COMPARE', '='), ('LBRACE', '{'), ('NEWLINE', '\n'),
        ])
        self.assertEqual([t.value for t in tokens if t.kind == 'STRING'],
                         ['E1', 'John', 'D1', 'E2', 'Alice', 'D2', 'E3', 'Bob', 'D1'])
        self.assertEqual([t.value for t in tokens if t.kind == 'NUMBER'], ['32', '28', '29'])
        self.assertEqual(sum(t.kind == 'NEWLINE' for t in tokens), 5)

    def test_whole_fields_and_quoted_values(self):
        source = "R(a)={\n32\n'32'\nE1\n32x\n1.\n1e3\na.b\na-b\n'O''Brien'\n'a,b) [x]'\n''\n'//text'\n}\n"
        values = [(t.kind, t.value) for t in tokenize(source, relations=True)
                  if t.kind in ('STRING', 'NUMBER')]
        self.assertEqual(values, [('NUMBER', '32'), ('STRING', '32'), ('STRING', 'E1'),
            ('STRING', '32x'), ('STRING', '1.'),
            ('STRING', '1e3'), ('STRING', 'a.b'), ('STRING', 'a-b'),
            ('STRING', "O'Brien"), ('STRING', 'a,b) [x]'), ('STRING', ''), ('STRING', '//text')])

    def test_positions_blank_lines_comments_and_multiple_tables(self):
        source = "\r\n // header\r\nR(a)={\r\n\r\n // row comment\r\n  Bob  \r\n}\r\nS(b)={\r\n}\r\n"
        tokens = tokenize(source, relations=True)
        bob = next(t for t in tokens if t.value == 'Bob')
        self.assertEqual((bob.offset, bob.line, bob.column, bob.text), (source.index('Bob'), 6, 3, 'Bob'))
        self.assertEqual([t.value for t in tokens if t.kind == 'NAME'], ['R', 'a', 'S', 'b'])
        self.assertEqual(sum(t.kind == 'NEWLINE' for t in tokens), 5)
        for token in tokens:
            self.assertEqual(source[token.offset:token.offset + len(token.text)], token.text)

    def test_decimal_field_is_rejected(self):
        with self.assertRaises(LexicalError) as result:
            tokenize('R(a)={\n-30.5\n}', relations=True)
        self.assertEqual((result.exception.line, result.exception.column), (2, 1))

    def test_text_requiring_quotes(self):
        for value in ('Bob Smith', 'Bob)', 'a{b', "O'Brien", "'Bob'x", 'A, //text'):
            with self.subTest(value=value), self.assertRaises(LexicalError):
                tokenize('R(a)={\n' + value + '\n}', relations=True)

    def test_unterminated_row_string_location(self):
        with self.assertRaises(LexicalError) as result:
            tokenize("R(a)={\n  'Bob\n}", relations=True)
        self.assertEqual((result.exception.offset, result.exception.line, result.exception.column), (9, 2, 3))

    def test_missing_values_left_for_parser(self):
        tokens = tokenize('R(a,b)={\n,\n}', relations=True)
        self.assertEqual([t.kind for t in tokens[-5:]], ['NEWLINE', 'COMMA', 'NEWLINE', 'RBRACE', 'EOF'])

    def test_query_does_not_accept_relation_braces(self):
        with self.assertRaises(LexicalError):
            tokenize('R(a)={\n1\n}')


class CommandLineTests(unittest.TestCase):
    def test_error_has_no_traceback(self):
        output = StringIO()
        with patch('sys.argv', ['ra', '--tokens', "select[Name='Bob](R)"]), redirect_stderr(output):
            self.assertEqual(main(), 1)
        self.assertIn('line 1, column 13', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())

    def test_relation_file_preserves_windows_offsets(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'relations.txt'
            path.write_bytes(b'R(a)={\r\n1\r\n}')
            output = StringIO()
            with patch('sys.argv', ['ra', '--tokens-file', str(path)]), redirect_stdout(output):
                self.assertEqual(main(), 0)
            self.assertIn("2:1 NUMBER '1'", output.getvalue())


if __name__ == '__main__':
    unittest.main()
