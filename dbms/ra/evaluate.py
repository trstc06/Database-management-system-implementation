"""Evaluate a relational algebra tree over any mapping of named sources."""

from time import perf_counter
from typing import Mapping
from ..common.errors import ExecutionError, ParseError
from . import operators as ops
from .operators import Source, Statistics, Table
from .parser import parse
from .tree import Node

SET_OPERATIONS = {'Union': ops.union, 'Intersect': ops.intersect, 'Minus': ops.except_}


def evaluate(tree: Node, tables: Mapping[str, Source], stats: Statistics | None = None) -> Source:
    """Build the operator pipeline for `tree`; no tuple is read yet."""
    if tree.kind == 'Relation':
        if tree.value not in tables:
            raise ExecutionError('Name', f'unknown relation {tree.value}')
        return tables[tree.value]
    if tree.kind == 'Distinct':
        return ops.distinct(evaluate(tree.children[0], tables, stats))
    if tree.kind in ('Select', 'Project', 'Rename'):
        parameter, child = tree.children
        source = evaluate(child, tables, stats)
        if tree.kind == 'Select':
            return ops.select(source, parameter, stats)
        if tree.kind == 'Project':
            return ops.project(source, tuple(attribute.value for attribute in parameter.children))
        return ops.rename(source, parameter.value)
    if tree.kind == 'Join':
        condition, left, right = tree.children
        return ops.join(evaluate(left, tables, stats), evaluate(right, tables, stats), condition, stats)
    left = evaluate(tree.children[0], tables, stats)
    right = evaluate(tree.children[1], tables, stats)
    if tree.kind == 'Times':
        return ops.times(left, right)
    return SET_OPERATIONS[tree.kind](left, right)


def execute(query: str, tables: Mapping[str, Source], stats: Statistics | None = None) -> Table:
    """Parse, evaluate and materialise a query, filling `stats` if given."""
    if stats is not None:
        stats.join_pairs = stats.selection_rows = stats.output_rows = 0
        stats.wall_seconds = 0.0
    tree = parse(query)
    started = perf_counter() if stats is not None else None
    try:
        result = ops.materialize(evaluate(tree, tables, stats))
    except RecursionError:
        # Point to the root operation of the tree that exceeded the limit.
        raise ParseError('query tree rooted here is too deep to execute', tree.token) from None
    if stats is not None:
        stats.wall_seconds = perf_counter() - started
        stats.output_rows = len(result.rows)
    return result


def format_relation(relation: Table) -> str:
    def display(value):
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
        return str(value)

    lines = ['(' + ', '.join(c.label for c in relation.columns) + ') = {']
    for row in relation.rows:
        lines.append('  ' + ', '.join(display(value) for value in row))
    lines.append('}')
    return '\n'.join(lines)
