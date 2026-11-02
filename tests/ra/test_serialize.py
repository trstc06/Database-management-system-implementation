"""JSON round trip for every node type (refactor point 4)."""

import json
import unittest
from dbms.common.errors import ExecutionError
from dbms.ra.parser import parse, parse_condition
from dbms.ra.serialize import condition_to_text, from_json, to_json

EVERY_QUERY_NODE = [
    'R',
    "select[R.a > 500 and R.b = 'cs'](R)",
    'project[R.a, b](R)',
    'rename[E](Emp)',
    'distinct(project[a](R))',
    'R times S',
    'Emp join[e.did = d.did] Dept',
    'R union S',
    'R intersect S',
    'R minus S',
]

EVERY_CONDITION_SHAPE = [
    'a = 1', 'a <> 1', 'a < 1', 'a <= -1', 'a > b', "a >= 'x'",
    "s = 'O''Brien'", 'not a = 1', 'not not a = 1', 'not (a = 1 and b = 2)',
    'a = 1 and b = 2 or c = 3', 'a = 1 and (b = 2 or c = 3)',
    'a = 1 or (b = 2 or c = 3)', '(a = 1 or b = 2) or c = 3',
    'a = 1 and (b = 2 and c = 3)', 'not.id = 3', 'not not = 3',
]


def round_trip(tree):
    return from_json(json.loads(json.dumps(to_json(tree))))


class SerializeTests(unittest.TestCase):
    def test_every_query_node_round_trips(self):
        for query in EVERY_QUERY_NODE:
            with self.subTest(query=query):
                tree = parse(query)
                self.assertEqual(round_trip(tree), tree)

    def test_every_condition_shape_round_trips_through_text(self):
        for text in EVERY_CONDITION_SHAPE:
            with self.subTest(text=text):
                tree = parse_condition(text)
                self.assertEqual(parse_condition(condition_to_text(tree)), tree)

    def test_printing_uses_only_needed_parentheses(self):
        self.assertEqual(condition_to_text(parse_condition('((a=1) and (b=2)) or (c=3)')),
                         'a = 1 and b = 2 or c = 3')
        self.assertEqual(condition_to_text(parse_condition('a=1 and (b=2 or c=3)')),
                         'a = 1 and (b = 2 or c = 3)')
        self.assertEqual(condition_to_text(parse_condition('a != 1')), 'a <> 1')

    def test_json_uses_logical_plan_shape(self):
        plan = to_json(parse("project[e.name](select[d.budget > 1000](rename[e](Emp) join[e.did = d.did] rename[d](Dept)))"))
        self.assertEqual(plan, {
            'op': 'project', 'columns': ['e.name'],
            'child': {'op': 'select', 'condition': 'd.budget > 1000',
                      'child': {'op': 'join', 'condition': 'e.did = d.did',
                                'left': {'op': 'rename', 'to': 'e', 'child': {'op': 'scan', 'table': 'Emp'}},
                                'right': {'op': 'rename', 'to': 'd', 'child': {'op': 'scan', 'table': 'Dept'}}}}})
        self.assertEqual(to_json(parse('R times S'))['op'], 'cross')
        self.assertEqual(to_json(parse('R minus S'))['op'], 'except')

    def test_malformed_plans_name_the_node_and_problem(self):
        cases = [
            ({'op': 'select', 'child': {'op': 'scan', 'table': 'R'}}, "Node 'select': missing required field 'condition'"),
            ({'op': 'select', 'condition': 5, 'child': {'op': 'scan', 'table': 'R'}}, "Node 'select': field 'condition' must be a string"),
            ({'op': 'merge_join', 'left': {}, 'right': {}}, "Node 'merge_join': unknown operator 'merge_join'"),
            ({'op': 'project', 'columns': [], 'child': {'op': 'scan', 'table': 'R'}}, "Node 'project': field 'columns' must be a nonempty list"),
            ({'op': 'project', 'columns': ['a.'], 'child': {'op': 'scan', 'table': 'R'}}, "Node 'project': column 'a.' is invalid"),
            ({'op': 'join', 'condition': 'a = ', 'left': {'op': 'scan', 'table': 'R'},
              'right': {'op': 'scan', 'table': 'S'}}, "Node 'join': field 'condition' is invalid"),
            ({'table': 'R'}, "missing its 'op'"),
            ([], 'must be a JSON object'),
        ]
        for plan, message in cases:
            with self.subTest(plan=plan), self.assertRaises(ExecutionError) as result:
                from_json(plan)
            self.assertIn(message, str(result.exception))
            self.assertEqual(result.exception.category, 'plan')


if __name__ == '__main__':
    unittest.main()
