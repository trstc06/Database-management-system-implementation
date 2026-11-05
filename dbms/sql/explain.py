"""Indented text for parse trees (sql parse) and logical plans (sql compile)."""

import json
from ..ra.serialize import condition_to_text
from ..ra.tree import Node
from . import ast
from .scope import COMPARISON_KINDS

INDENT = '  '


# Parse trees -------------------------------------------------------------

def raw_condition(node) -> Node:
    """A condition as written, names unresolved, as an ra tree for printing."""
    if isinstance(node, ast.Logical):
        return Node(node.op.capitalize(), children=tuple(raw_condition(c) for c in node.operands))
    return Node(COMPARISON_KINDS[node.op], children=(raw_operand(node.left), raw_operand(node.right)))


def raw_operand(expr) -> Node:
    if isinstance(expr, ast.Literal):
        return Node('Num', str(expr.value)) if isinstance(expr.value, int) else Node('Str', expr.value)
    return Node('Attr', expr.text())


def condition_text(node) -> str:
    return condition_to_text(raw_condition(node))


def item_text(item: ast.SelectItem) -> str:
    text = item.expr.text()
    return f'{text} AS {item.alias}' if item.alias else text


def explain_statement(node, depth: int = 0) -> list[str]:
    pad = INDENT * depth
    if isinstance(node, ast.CreateTable):
        columns = ', '.join(f'{c.name} {c.type}' + (f'({c.length})' if c.length else '') for c in node.columns)
        return [f'{pad}create table {node.table} ({columns})']
    if isinstance(node, ast.Insert):
        values = ', '.join(json.dumps(v.value) if isinstance(v.value, int) else
                           "'" + v.value.replace("'", "''") + "'" for v in node.values)
        return [f'{pad}insert into {node.table} values ({values})']
    if isinstance(node, ast.SetOperation):
        return ([f'{pad}{node.op}'] + explain_statement(node.left, depth + 1)
                + explain_statement(node.right, depth + 1))
    return explain_block(node, depth)


def explain_block(block: ast.SelectBlock, depth: int) -> list[str]:
    pad, inner = INDENT * depth, INDENT * (depth + 1)
    items = '*' if isinstance(block.items, ast.Star) else ', '.join(item_text(i) for i in block.items)
    lines = [f'{pad}select_block' + (' distinct' if block.distinct else ''), f'{inner}select: {items}',
             f'{inner}from' + (' (join)' if block.source.uses_join else '')]
    for index, ref in enumerate(block.source.refs):
        on = ''
        if block.source.uses_join and index > 0:
            on = ' on ' + condition_text(block.source.conditions[index - 1])
        if isinstance(ref, ast.SubqueryRef):
            lines.append(f'{inner}{INDENT}subquery as {ref.alias}{on}')
            lines.extend(explain_statement(ref.query, depth + 3))
        else:
            lines.append(f'{inner}{INDENT}table {ref.name}' + (f' as {ref.alias}' if ref.alias else '') + on)
    if block.where is not None:
        lines.append(f'{inner}where: {condition_text(block.where)}')
    if block.group_by:
        lines.append(f'{inner}group by: ' + ', '.join(c.text() for c in block.group_by))
    if block.having is not None:
        lines.append(f'{inner}having: {condition_text(block.having)}')
    if block.order_by:
        lines.append(f'{inner}order by: ' + ', '.join(
            o.column.text() + (' desc' if o.descending else ' asc') for o in block.order_by))
    if block.limit is not None:
        lines.append(f'{inner}limit: {block.limit}')
    return lines


# Logical plans -----------------------------------------------------------

def node_detail(plan: dict) -> str:
    op = plan['op']
    if op == 'scan':
        return f"scan {plan['table']}"
    if op == 'rename':
        return f"rename {plan['to']}"
    if op in ('select', 'join'):
        return f"{op} {plan['condition']}"
    if op == 'project':
        aliases = plan.get('aliases') or [None] * len(plan['columns'])
        columns = [c if a is None else f'{c} AS {a}' for c, a in zip(plan['columns'], aliases)]
        return f"project [{', '.join(columns)}]"
    if op == 'group_by':
        aggregates = ', '.join(f'{f}({a}) AS {n}' for f, a, n in plan['aggregates'])
        return f"group_by keys [{', '.join(plan['keys'])}] aggregates [{aggregates}]"
    if op == 'sort':
        return 'sort ' + ', '.join(f'{c} {d}' for c, d in plan['keys'])
    if op == 'limit':
        return f"limit {plan['n']}"
    if op == 'create_table':
        return f"create_table {plan['table']} {json.dumps(plan['columns'])}"
    if op == 'insert':
        return f"insert {plan['table']} {json.dumps(plan['values'])}"
    return op


def explain_plan(plan: dict, depth: int = 0) -> list[str]:
    lines = [INDENT * depth + node_detail(plan)]
    for key in ('child', 'left', 'right'):
        if key in plan:
            lines.extend(explain_plan(plan[key], depth + 1))
    return lines
