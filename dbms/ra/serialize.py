"""Write relational algebra trees as JSON and read them back.

The JSON shape is the logical plan contract from Module 3: every node is an
object with an "op" field, unary nodes have "child", binary nodes have
"left" and "right", and conditions travel as strings in the same syntax the
condition parser reads. `to_json` and `from_json` are inverses: reading back
what was written gives an equal tree.
"""

from ..common.errors import DbError, ExecutionError
from .parser import parse_attribute, parse_condition
from .tree import COMPARISONS, Node

OPERATORS = {'Eq': '=', 'Ne': '<>', 'Lt': '<', 'Le': '<=', 'Gt': '>', 'Ge': '>='}
PRECEDENCE = {'Or': 1, 'And': 2, 'Not': 3}
COMPARISON_PRECEDENCE = 4

BINARY_OPS = {'Times': 'cross', 'Union': 'union', 'Intersect': 'intersect', 'Minus': 'except'}
BINARY_KINDS = {op: kind for kind, op in BINARY_OPS.items()}


# Conditions --------------------------------------------------------------

def precedence(node: Node) -> int:
    return PRECEDENCE.get(node.kind, COMPARISON_PRECEDENCE)


def operand_text(node: Node) -> str:
    if node.kind == 'Str':
        return "'" + node.value.replace("'", "''") + "'"
    return str(node.value)


def condition_to_text(node: Node, minimum: int = 0) -> str:
    """Print a condition with only the parentheses its structure needs.

    `minimum` is the precedence the surrounding operator requires. And and
    Or group to the left, so a right operand at the same level keeps its
    parentheses.
    """
    if node.kind in COMPARISONS:
        left, right = node.children
        text = f'{operand_text(left)} {OPERATORS[node.kind]} {operand_text(right)}'
    elif node.kind == 'Not':
        text = 'not ' + condition_to_text(node.children[0], PRECEDENCE['Not'])
    else:
        level = PRECEDENCE[node.kind]
        left = condition_to_text(node.children[0], level)
        right = condition_to_text(node.children[1], level + 1)
        text = f'{left} {node.kind.lower()} {right}'
    return f'({text})' if precedence(node) < minimum else text


# Query trees -------------------------------------------------------------

def to_json(node: Node) -> dict:
    """Convert a query tree into a JSON-ready dict."""
    if node.kind == 'Relation':
        return {'op': 'scan', 'table': node.value}
    if node.kind == 'Distinct':
        return {'op': 'distinct', 'child': to_json(node.children[0])}
    if node.kind in ('Select', 'Project', 'Rename'):
        parameter, child = node.children
        if node.kind == 'Select':
            fields = {'condition': condition_to_text(parameter)}
        elif node.kind == 'Project':
            fields = {'columns': [attribute.value for attribute in parameter.children]}
        else:
            fields = {'to': parameter.value}
        return {'op': node.kind.lower(), **fields, 'child': to_json(child)}
    if node.kind == 'Join':
        condition, left, right = node.children
        return {'op': 'join', 'condition': condition_to_text(condition),
                'left': to_json(left), 'right': to_json(right)}
    if node.kind in BINARY_OPS:
        left, right = node.children
        return {'op': BINARY_OPS[node.kind], 'left': to_json(left), 'right': to_json(right)}
    raise ExecutionError('Plan', f'cannot serialise a {node.kind} node as a query')


def plan_error(op: str, problem: str) -> ExecutionError:
    return ExecutionError('Plan', f"Node '{op}': {problem}")


def field(plan: dict, op: str, name: str, kind: type):
    if name not in plan:
        raise plan_error(op, f"missing required field '{name}'")
    value = plan[name]
    if not isinstance(value, kind):
        raise plan_error(op, f"field '{name}' must be a {'string' if kind is str else kind.__name__}")
    return value


def read_text(op: str, what: str, text: str, reader) -> Node:
    try:
        return reader(text)
    except DbError as error:
        raise plan_error(op, f'{what} is invalid: {error}') from None


def text_field(plan: dict, op: str, name: str, reader) -> Node:
    return read_text(op, f"field '{name}'", field(plan, op, name, str), reader)


def from_json(plan: dict) -> Node:
    """Read back a dict written by `to_json`, rejecting malformed nodes."""
    if not isinstance(plan, dict):
        raise ExecutionError('Plan', 'every plan node must be a JSON object')
    op = plan.get('op')
    if not isinstance(op, str):
        raise ExecutionError('Plan', "a plan node is missing its 'op' string")
    if op == 'scan':
        return Node('Relation', field(plan, op, 'table', str))
    if op in BINARY_KINDS or op == 'join':
        left = from_json(field(plan, op, 'left', dict))
        right = from_json(field(plan, op, 'right', dict))
        if op == 'join':
            condition = text_field(plan, op, 'condition', parse_condition)
            return Node('Join', children=(condition, left, right))
        return Node(BINARY_KINDS[op], children=(left, right))
    if op not in ('select', 'project', 'rename', 'distinct'):
        raise plan_error(op, f"unknown operator '{op}'")
    child = from_json(field(plan, op, 'child', dict))
    if op == 'distinct':
        return Node('Distinct', children=(child,))
    if op == 'select':
        parameter = text_field(plan, op, 'condition', parse_condition)
    elif op == 'project':
        columns = field(plan, op, 'columns', list)
        if not columns or not all(isinstance(column, str) for column in columns):
            raise plan_error(op, "field 'columns' must be a nonempty list of strings")
        parameter = Node('Attributes', children=tuple(read_text(op, f'column {c!r}', c, parse_attribute)
                                                      for c in columns))
    else:
        parameter = Node('Alias', field(plan, op, 'to', str))
    return Node(op.capitalize(), children=(parameter, child))
