"""The standalone condition parser and evaluator that Modules 2 to 4 reuse."""

import unittest
from dbms.common.errors import ExecutionError, ParseError
from dbms.common.schema import INT, VARCHAR, Column
from dbms.ra.conditions import bind_condition
from dbms.ra.parser import parse, parse_condition
from dbms.ra.tree import Node

COLUMNS = (Column('R', 'a', INT), Column('R', 'b', VARCHAR, 20), Column('S', 'a', INT))


def holds(condition, row):
    return bind_condition(parse_condition(condition), COLUMNS)(row)


class ConditionParserTests(unittest.TestCase):
    def test_plan_condition_string_parses_on_its_own(self):
        tree = parse_condition("R.a > 500 and R.b = 'cs'")
        self.assertEqual(tree, Node('And', children=(
            Node('Gt', children=(Node('Attr', 'R.a'), Node('Num', '500'))),
            Node('Eq', children=(Node('Attr', 'R.b'), Node('Str', 'cs'))))))

    def test_same_tree_as_inside_a_select(self):
        text = 'not (a = 1 and b = 2) or c > 3'
        self.assertEqual(parse_condition(text), parse(f'select[{text}](R)').children[0])

    def test_keywords_ignore_case_and_sql_not_equal(self):
        self.assertEqual(parse_condition("R.a <> 1 AND NOT R.b = 'x' Or S.a >= 2"),
                         parse_condition("R.a != 1 and not R.b = 'x' or S.a >= 2"))

    def test_trailing_text_and_empty_input_are_errors(self):
        for text in ['', 'a = 1 b', 'a = 1 and', 'a', '(a = 1']:
            with self.subTest(text=text), self.assertRaises(ParseError):
                parse_condition(text)


class ConditionEvaluationTests(unittest.TestCase):
    def test_comparisons_on_both_types(self):
        row = (5, 'cs', 7)
        self.assertTrue(holds("R.a < S.a and R.b = 'cs'", row))
        self.assertTrue(holds("R.b < 'd' and R.b >= 'cs'", row))
        self.assertFalse(holds('R.a <> 5', row))
        self.assertTrue(holds('not R.a > 5 or S.a = 0', row))

    def test_varchar_order_is_utf8_byte_order(self):
        # 'é' (U+00E9, bytes C3 A9) sorts after 'z' (7A) in both orders.
        self.assertTrue(holds("R.b > 'z'", (1, 'é', 1)))
        self.assertLess('z'.encode(), 'é'.encode())

    def test_int_against_varchar_is_a_type_error_even_for_equality(self):
        for text in ["R.a = 'x'", 'R.b = 1', 'R.a <> R.b', "1 = 1 or R.a = '1'"]:
            with self.subTest(text=text), self.assertRaises(ExecutionError) as result:
                bind_condition(parse_condition(text), COLUMNS)
            self.assertEqual(result.exception.category, 'type')

    def test_unknown_and_ambiguous_columns(self):
        with self.assertRaises(ExecutionError) as result:
            bind_condition(parse_condition('a = 1'), COLUMNS)
        self.assertIn('ambiguous attribute a', str(result.exception))
        self.assertIn('R.a, S.a', str(result.exception))
        with self.assertRaises(ExecutionError) as result:
            bind_condition(parse_condition('T.a = 1'), COLUMNS)
        self.assertIn('unknown attribute T.a', str(result.exception))


if __name__ == '__main__':
    unittest.main()
