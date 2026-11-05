"""Bind a condition tree to a schema and evaluate it against tuples.

Binding resolves every column and type-checks every comparison before any
tuple is read, so errors appear even on empty inputs and in branches that
short-circuit. Comparison is same-type only: INT against VARCHAR is an
error for every operator, including equality. Python compares str by code
point, which is the same order as comparing the UTF-8 bytes.
"""

import operator
from typing import Callable
from ..common.errors import ExecutionError
from ..common.schema import INT, VARCHAR, Column, resolve, type_label
from .tree import Node

Predicate = Callable[[tuple], bool]

NUMERIC = (INT, 'REAL')   # AVG results compare with whole numbers

COMPARE = {'Eq': operator.eq, 'Ne': operator.ne, 'Lt': operator.lt,
           'Le': operator.le, 'Gt': operator.gt, 'Ge': operator.ge}


def literal_value(node: Node) -> int | str:
    return int(node.value) if node.kind == 'Num' else node.value


def literal_type(node: Node) -> str:
    return INT if node.kind == 'Num' else VARCHAR


def bind_operand(node: Node, columns: tuple[Column, ...]):
    """Return (type, getter) for a column reference or a literal."""
    if node.kind == 'Attr':
        index = resolve(node.value, columns)
        return columns[index].type, lambda row: row[index]
    value = literal_value(node)
    return literal_type(node), lambda row: value


def bind_condition(node: Node, columns: tuple[Column, ...]) -> Predicate:
    """Resolve and type-check every branch, then return a row predicate."""
    if node.kind == 'Not':
        child = bind_condition(node.children[0], columns)
        return lambda row: not child(row)
    if node.kind in ('And', 'Or'):
        left = bind_condition(node.children[0], columns)
        right = bind_condition(node.children[1], columns)
        if node.kind == 'And':
            return lambda row: left(row) and right(row)
        return lambda row: left(row) or right(row)
    left_type, left = bind_operand(node.children[0], columns)
    right_type, right = bind_operand(node.children[1], columns)
    both_numeric = left_type in NUMERIC and right_type in NUMERIC
    if left_type is not None and right_type is not None and left_type != right_type and not both_numeric:
        raise ExecutionError('Type', f'cannot compare {type_label(left_type)} with {type_label(right_type)}')
    compare = COMPARE[node.kind]
    return lambda row: compare(left(row), right(row))

