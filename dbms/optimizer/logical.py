"""Helpers for logical plans: output columns, condition pieces, name mapping.

Plans are plain dicts in the Module 3 shape. Nothing here changes a plan
in place; every function returns new dicts.
"""

from ..common.errors import DbError
from ..ra.parser import parse_condition
from ..ra.serialize import condition_to_text
from ..ra.tree import Node

UNARY = ('select', 'project', 'rename', 'group_by', 'distinct', 'sort', 'limit')
BINARY = ('join', 'cross', 'union', 'union_all', 'intersect', 'except')
SET_OPS = ('union', 'union_all', 'intersect', 'except')


def base_name(name: str) -> str:
    return name.rsplit('.', 1)[-1]


def outputs(plan: dict, tables: dict) -> list[str]:
    """Output column names of a logical node, in order. tables: {name: [column names]}."""
    op = plan['op']
    if op == 'scan':
        if plan['table'] not in tables:
            raise DbError('name', f"unknown table '{plan['table']}'")
        return [f"{plan['table']}.{c}" for c in tables[plan['table']]]
    if op == 'rename':
        return [f"{plan['to']}.{base_name(c)}" for c in outputs(plan['child'], tables)]
    if op == 'project':
        aliases = plan.get('aliases') or [None] * len(plan['columns'])
        return [a or c for c, a in zip(plan['columns'], aliases)]
    if op == 'group_by':
        return list(plan['keys']) + [a[2] if len(a) == 3 else default_aggregate_name(a) for a in plan['aggregates']]
    if op in ('join', 'cross'):
        return outputs(plan['left'], tables) + outputs(plan['right'], tables)
    if op in SET_OPS:
        return outputs(plan['left'], tables)
    return outputs(plan['child'], tables)


def default_aggregate_name(spec: list) -> str:
    function, argument = spec[0], spec[1]
    return 'count_star' if argument == '*' else f"{function}_{argument.replace('.', '_')}"


# Conditions --------------------------------------------------------------

def parse(text: str) -> Node:
    return parse_condition(text)


def text(node: Node) -> str:
    return condition_to_text(node)


def conjuncts(node: Node) -> list[Node]:
    if node.kind == 'And':
        return conjuncts(node.children[0]) + conjuncts(node.children[1])
    return [node]


def conjoin(nodes: list[Node]) -> Node:
    result = nodes[0]
    for node in nodes[1:]:
        result = Node('And', children=(result, node))
    return result


def attributes(node: Node) -> set[str]:
    if node.kind == 'Attr':
        return {node.value}
    return set().union(*(attributes(child) for child in node.children)) if node.children else set()


def rename_attributes(node: Node, mapping: dict) -> Node:
    if node.kind == 'Attr':
        return Node('Attr', mapping.get(node.value, node.value))
    return Node(node.kind, node.value, tuple(rename_attributes(c, mapping) for c in node.children))


def is_equality_between(node: Node, left: list[str], right: list[str]) -> bool:
    if node.kind != 'Eq' or any(c.kind != 'Attr' for c in node.children):
        return False
    a, b = node.children[0].value, node.children[1].value
    return (a in left and b in right) or (a in right and b in left)


def select(child: dict, condition: Node) -> dict:
    return {'op': 'select', 'condition': text(condition), 'child': child}


def project(child: dict, columns: list[str]) -> dict:
    return {'op': 'project', 'columns': list(columns), 'child': child}


def with_children(plan: dict, **children) -> dict:
    return {**plan, **children}


def children(plan: dict) -> list[str]:
    return [key for key in ('child', 'left', 'right') if key in plan]
