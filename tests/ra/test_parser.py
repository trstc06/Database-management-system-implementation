"""Required parsing cases and checks for the documented grammar."""

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch
from dbms.common.errors import LexicalError, ParseError
from dbms.ra.cli import main
from dbms.ra.parser import parse
from dbms.ra.tree import Node, format_tree


def relation(name):
    return Node('Relation', name)


def binary(kind, left, right):
    return Node(kind, children=(left, right))


def comparison(kind, name, number):
    return binary(kind, Node('Attr', name), Node('Num', number))


class ParserTests(unittest.TestCase):
    def test_01_02_same_tree_without_spaces(self):
        expected = Node('Select', children=(comparison('Eq', 'x1', '3'), relation('R')))
        self.assertEqual(parse('select[x1=3](R)'), expected)
        self.assertEqual(parse('select[ x1 = 3 ](R)'), expected)
        self.assertEqual(format_tree(parse('select[x1=3](R)')),
                         format_tree(parse('select[ x1 = 3 ](R)')))

    def test_03_to_08_complete_queries(self):
        cases = [('Age>=30', comparison('Ge', 'Age', '30')),
                 ('Age>-30', comparison('Gt', 'Age', '-30')),
                 ("Name='Bob)'", binary('Eq', Node('Attr', 'Name'), Node('Str', 'Bob)'))),
                 ("Name='a,b'", binary('Eq', Node('Attr', 'Name'), Node('Str', 'a,b'))),
                 ("Name='O''Brien'", binary('Eq', Node('Attr', 'Name'), Node('Str', "O'Brien"))),
                 ('union=3', comparison('Eq', 'union', '3'))]
        for text, condition in cases:
            with self.subTest(text=text):
                self.assertEqual(parse(f'select[{text}](R)'), Node('Select', children=(condition, relation('R'))))

    def test_09_unclosed_string(self):
        with self.assertRaises(LexicalError):
            parse("select[Name='Bob](R)")

    def test_10_union_minus(self):
        expected = binary('Minus', binary('Union', relation('A'), relation('B')), relation('C'))
        self.assertEqual(parse('A union B minus C'), expected)
        self.assertEqual(format_tree(expected), 'Minus\n|-- Union\n|   |-- Relation(A)\n|   `-- Relation(B)\n`-- Relation(C)')

    def test_11_left_associative_minus(self):
        self.assertEqual(parse('A minus B minus C'),
                         binary('Minus', binary('Minus', relation('A'), relation('B')), relation('C')))
        self.assertNotEqual(parse('A minus B minus C'), parse('A minus (B minus C)'))

    def test_12_not_and_or(self):
        condition = binary('Or', Node('Not', children=(binary('And', comparison('Eq', 'a', '1'),
                          comparison('Eq', 'b', '2')),)), comparison('Gt', 'c', '3'))
        self.assertEqual(parse('select[not (a=1 and b=2) or c>3](R)').children[0], condition)

    def test_13_and_before_or(self):
        expected = binary('Or', binary('And', comparison('Eq', 'a', '1'),
                          comparison('Eq', 'b', '2')), comparison('Eq', 'c', '3'))
        self.assertEqual(parse('select[a=1 and b=2 or c=3](R)').children[0], expected)

    def test_14_nested_operations(self):
        inner = Node('Select', children=(binary('Eq', Node('Attr', 'DID'), Node('Str', 'D1')), relation('Employees')))
        outer = Node('Select', children=(comparison('Gt', 'Age', '30'), inner))
        expected = Node('Project', children=(Node('Attributes', children=(Node('Attr', 'Name'),)), outer))
        self.assertEqual(parse("project[Name](select[Age>30](select[DID='D1'](Employees)))"), expected)

    def test_15_parentheses(self):
        self.assertEqual(parse('(A union B) minus (C intersect D)'),
                         binary('Minus', binary('Union', relation('A'), relation('B')),
                                binary('Intersect', relation('C'), relation('D'))))

    def test_16_missing_parenthesis(self):
        source = 'select[Age>30](R'
        with self.assertRaises(ParseError) as result:
            parse(source)
        self.assertIn("closing ')'", str(result.exception))
        self.assertEqual((result.exception.offset, result.exception.column), (len(source), len(source) + 1))

    def test_17_empty_projection(self):
        with self.assertRaises(ParseError) as result:
            parse('project[](R)')
        self.assertEqual(result.exception.column, 9)
        self.assertIn('list cannot be empty', str(result.exception))

    def test_join_rename_and_all_precedence_levels(self):
        tree = parse('A union B intersect C times D join[C.id=E.id] rename[E](R)')
        self.assertEqual(tree.kind, 'Union')
        intersection = tree.children[1]
        self.assertEqual(intersection.kind, 'Intersect')
        join = intersection.children[1]
        self.assertEqual(join.kind, 'Join')
        self.assertEqual(join.children[1], binary('Times', relation('C'), relation('D')))
        self.assertEqual(join.children[0], binary('Eq', Node('Attr', 'C.id'), Node('Attr', 'E.id')))
        self.assertEqual(join.children[2], Node('Rename', children=(Node('Alias', 'E'), relation('R'))))

    def test_contextual_keywords(self):
        self.assertEqual(parse('union union select'), binary('Union', relation('union'), relation('select')))
        self.assertEqual(parse('select[not not=3](R)').children[0],
                         Node('Not', children=(comparison('Eq', 'not', '3'),)))
        self.assertEqual(parse('select[not.id=3](R)').children[0], comparison('Eq', 'not.id', '3'))
        self.assertEqual(parse('select[not not a=1](R)').children[0],
                         Node('Not', children=(Node('Not', children=(comparison('Eq', 'a', '1'),)),)))

    def test_every_comparison(self):
        for operator, kind in [('=', 'Eq'), ('!=', 'Ne'), ('<>', 'Ne'), ('<', 'Lt'), ('<=', 'Le'), ('>', 'Gt'), ('>=', 'Ge')]:
            with self.subTest(operator=operator):
                self.assertEqual(parse(f'select[A{operator}B](R)').children[0],
                                 binary(kind, Node('Attr', 'A'), Node('Attr', 'B')))

    def test_invalid_syntax(self):
        cases = ['', 'A union', 'A B', 'A)', '()', 'select[a=1(R)',
                 'select[a=1=2](R)', 'select[a](R)', 'project[a,](R)',
                 'project[R.](R)', 'rename[](R)', 'rename[A,B](R)',
                 'A join[] B', 'A join[a=1]', 'select[not](R)']
        for source in cases:
            with self.subTest(source=source), self.assertRaises(ParseError):
                parse(source)

    def test_multiline_error_position_and_node_position(self):
        with self.assertRaises(ParseError) as result:
            parse('A union\n)')
        self.assertEqual((result.exception.line, result.exception.column), (2, 1))
        self.assertEqual(parse('A\n union B').token.line, 2)

    def test_long_chain_prints_without_recursion(self):
        tree = parse(' minus '.join(['A'] * 300))
        self.assertEqual(format_tree(tree).count('Relation(A)'), 300)

    def test_excessive_nesting_reports_error(self):
        with self.assertRaises(ParseError) as result:
            parse('(' * 1500 + 'A' + ')' * 1500)
        self.assertIn('nesting is too deep', str(result.exception))


class RelationParserTests(unittest.TestCase):
    def test_tables_and_empty_table(self):
        tree = parse("// data\nR(a,b)={\n1, 'O''Brien'\n2, Bob\n}\nS(c)={\n}\n", relations=True)
        self.assertEqual(tree.kind, 'RelationFile')
        self.assertEqual(tree.children[0].value, ('R', ('a', 'b')))
        self.assertEqual(tree.children[0].children[0], Node('Row', children=(Node('Num', '1'), Node('Str', "O'Brien"))))
        self.assertEqual(tree.children[1], Node('Definition', ('S', ('c',))))
        self.assertIn('Definition(R, columns=[a, b])', format_tree(tree))

    def test_invalid_table_syntax(self):
        for source in ['R()={\n}', 'R(a)!={\n}', 'R(a)={1\n}', 'R(a)={\n1}',
                       'R(a)={\n,\n}', 'R(a)={\n1,\n}', 'R(a)={\n1\n', 'R(a)={\n}S(b)={\n}']:
            with self.subTest(source=source), self.assertRaises(ParseError):
                parse(source, relations=True)


class ParserCommandTests(unittest.TestCase):
    def test_tree_without_existing_relations(self):
        output = StringIO()
        with patch('sys.argv', ['ra', '--tree', 'Unknown union Other']), redirect_stdout(output):
            self.assertEqual(main(), 0)
        self.assertEqual(output.getvalue(), 'Union\n|-- Relation(Unknown)\n`-- Relation(Other)\n')

    def test_cli_errors_without_tracebacks(self):
        for source in ['project[](R)', "select[Name='Bob](R)"]:
            output = StringIO()
            with patch('sys.argv', ['ra', '--tree', source]), redirect_stderr(output):
                self.assertEqual(main(), 1)
            self.assertIn('line 1, column', output.getvalue())
            self.assertNotIn('Traceback', output.getvalue())


if __name__ == '__main__':
    unittest.main()
