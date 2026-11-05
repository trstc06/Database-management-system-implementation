"""Lexer and parser: Section 1 rules, precedence, statements and syntax errors."""

import unittest
from dbms.common.errors import DbError
from dbms.sql import ast
from dbms.sql.lexer import tokenize
from dbms.sql.parser import parse, parse_query


def kinds(source: str) -> list[tuple[str, str]]:
    return [(t.kind, t.value) for t in tokenize(source)][:-1]


class LexerTests(unittest.TestCase):
    def test_keywords_fold_case_but_identifiers_do_not(self):
        self.assertEqual(kinds('SeLeCt Emp emp'), [('keyword', 'SELECT'), ('ident', 'Emp'), ('ident', 'emp')])

    def test_strings_numbers_and_operators(self):
        self.assertEqual(kinds("'it''s' -5 +7 <= >= <> < ="),
                         [('string', "it's"), ('int', '-5'), ('int', '7'), ('symbol', '<='),
                          ('symbol', '>='), ('symbol', '<>'), ('symbol', '<'), ('symbol', '=')])

    def test_comments_and_positions(self):
        tokens = tokenize('-- a comment\n  SELECT')
        self.assertEqual((tokens[0].value, tokens[0].line, tokens[0].column), ('SELECT', 2, 3))

    def test_lexical_errors(self):
        for source in ("'open", '1.5', 'a != b', '#', '12ab'):
            with self.subTest(source), self.assertRaises(DbError) as caught:
                tokenize(source)
            self.assertEqual(caught.exception.category, 'lexical')


class ParserTests(unittest.TestCase):
    def condition(self, text: str):
        return parse_query(f'SELECT a FROM T WHERE {text}').where

    def test_not_binds_tighter_than_and_which_binds_tighter_than_or(self):
        node = self.condition('NOT a = 1 AND b = 2 OR c = 3')
        self.assertEqual(node.op, 'or')
        self.assertEqual(node.operands[0].op, 'and')
        self.assertEqual(node.operands[0].operands[0].op, 'not')

    def test_parentheses_override_precedence(self):
        self.assertEqual(self.condition('a = 1 AND (b = 2 OR c = 3)').op, 'and')

    def test_set_operators_are_left_associative(self):
        node = parse_query('SELECT a FROM A UNION SELECT a FROM B EXCEPT SELECT a FROM C')
        self.assertEqual((node.op, node.left.op), ('except', 'union'))
        nested = parse_query('SELECT a FROM A UNION (SELECT a FROM B EXCEPT SELECT a FROM C)')
        self.assertEqual((nested.op, nested.right.op), ('union', 'except'))

    def test_several_statements_in_one_file(self):
        statements = parse('CREATE TABLE T (a INT, b VARCHAR(5)); INSERT INTO T VALUES (1, \'x\');\n'
                           'SELECT a FROM T;')
        self.assertEqual([type(s) for s in statements], [ast.CreateTable, ast.Insert, ast.SelectBlock])

    def test_aliases_with_and_without_as(self):
        source = parse_query('SELECT e.a FROM Emp e, Dept AS d, (SELECT a FROM T) t').source
        self.assertEqual([r.alias for r in source.refs], ['e', 'd', 't'])

    def test_syntax_errors_say_what_was_expected(self):
        cases = {
            'SELECT FROM T': 'a column name',
            'SELECT a FROM T WHERE a': 'a comparison operator',
            'SELECT a FROM (SELECT a FROM T)': 'an alias for the subquery',
            'SELECT a FROM T, U JOIN V ON a = b': "','",
            'SELECT a FROM T LIMIT -1': 'LIMIT needs',
            'SELECT a FROM T; garbage': 'SELECT, CREATE TABLE or INSERT INTO',
        }
        for source, expected in cases.items():
            with self.subTest(source), self.assertRaises(DbError) as caught:
                parse(source)
            self.assertEqual(caught.exception.category, 'syntax')
            self.assertIn(expected, caught.exception.message)


if __name__ == '__main__':
    unittest.main()
