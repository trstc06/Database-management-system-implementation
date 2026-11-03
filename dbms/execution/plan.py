"""Turn a physical plan (parsed JSON) into an operator tree.

Every node is checked before any operator is built, and errors name the
node and where it is, e.g. "node 'filter' at root.child: missing field
'condition'". Building resolves every table and column, so a bad name is
rejected before any page is read.
"""

from ..common.errors import DbError, ExecutionError
from .aggregate import Distinct, GroupBy
from .joins import HashJoin, NestedLoopJoin, SortMergeJoin
from .pipeline import Filter, Limit, Project, SeqScan, UnionAll
from .setops import Except, Intersect, Union
from .sort import Sort

# op -> (required fields with their JSON type, child fields)
SHAPES = {
    'seqscan': ({'table': str}, ()),
    'filter': ({'condition': str}, ('child',)),
    'project': ({'columns': list}, ('child',)),
    'distinct': ({'mode': str}, ('child',)),
    'sort': ({'keys': list}, ('child',)),
    'group_by': ({'mode': str, 'keys': list, 'aggregates': list}, ('child',)),
    'limit': ({}, ('child',)),
    'nested_loop_join': ({'condition': str}, ('left', 'right')),
    'sort_merge_join': ({'condition': str}, ('left', 'right')),
    'hash_join': ({'condition': str}, ('left', 'right')),
    'union_all': ({}, ('left', 'right')),
    'union': ({}, ('left', 'right')),
    'intersect': ({}, ('left', 'right')),
    'except': ({}, ('left', 'right')),
}
LIMIT_FIELDS = ('n', 'count', 'limit')
JOINS = {'nested_loop_join': NestedLoopJoin, 'sort_merge_join': SortMergeJoin, 'hash_join': HashJoin}
SET_OPS = {'union_all': UnionAll, 'union': Union, 'intersect': Intersect, 'except': Except}


def fail(op: str, path: str, message: str) -> ExecutionError:
    return ExecutionError('plan', f"node '{op}' at {path}: {message}")


def check_node(node, path: str) -> str:
    if not isinstance(node, dict):
        raise ExecutionError('plan', f'node at {path} must be a JSON object')
    op = node.get('op')
    if op not in SHAPES:
        raise ExecutionError('plan', f'node at {path} has unknown op {op!r}')
    fields, children = SHAPES[op]
    for field, kind in fields.items():
        if field not in node:
            raise fail(op, path, f"missing required field '{field}'")
        if not isinstance(node[field], kind):
            raise fail(op, path, f"field '{field}' must be a {kind.__name__}")
    for child in children:
        if child not in node:
            raise fail(op, path, f"missing required field '{child}'")
    if op == 'limit' and not any(isinstance(node.get(f), int) for f in LIMIT_FIELDS):
        raise fail(op, path, "needs a whole number field 'n'")
    allowed = set(fields) | set(children) | {'op'} | (set(LIMIT_FIELDS) if op == 'limit' else set())
    extra = sorted(set(node) - allowed - {'est_rows', 'est_pages', 'est_cost', 'alias'})
    if extra:
        raise fail(op, path, f"unexpected field '{extra[0]}'")
    return op


def build(ctx, node, path: str = 'root'):
    op = check_node(node, path)
    kids = {name: build(ctx, node[name], f'{path}.{name}') for name in SHAPES[op][1]}
    try:
        return make(ctx, op, node, kids)
    except DbError as error:
        if error.category == 'plan':
            raise fail(op, path, error.message) from None
        raise


def make(ctx, op: str, node: dict, kids: dict):
    if op == 'seqscan':
        return SeqScan(ctx, node['table'])
    if op == 'filter':
        return Filter(ctx, node['condition'], kids['child'])
    if op == 'project':
        return Project(ctx, node['columns'], kids['child'])
    if op == 'distinct':
        return Distinct(ctx, node['mode'], kids['child'])
    if op == 'sort':
        return Sort(ctx, node['keys'], kids['child'])
    if op == 'group_by':
        return GroupBy(ctx, node['mode'], node['keys'], node['aggregates'], kids['child'])
    if op == 'limit':
        count = next(node[f] for f in LIMIT_FIELDS if isinstance(node.get(f), int))
        return Limit(ctx, count, kids['child'])
    if op in JOINS:
        return JOINS[op](ctx, node['condition'], kids['left'], kids['right'])
    return SET_OPS[op](ctx, kids['left'], kids['right'])


def plan_nodes(root) -> list:
    """(depth, operator) in plan order; internal helpers are not plan nodes."""
    out = []
    def walk(operator, depth: int) -> None:
        out.append((depth, operator))
        for child in operator.children:
            walk(child, depth + 1)
    walk(root, 0)
    return out
