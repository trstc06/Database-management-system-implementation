"""Turn Bonus 1 relation-file text into in-memory tables.

Takes text, never a path: the caller reads the file. Tables are bags, so a
repeated row stays repeated. Column types come from the values: a number
makes the column INT, text makes it VARCHAR, and an empty table's columns
have no known type.
"""

from dataclasses import replace
from ..common.errors import ExecutionError
from ..common.schema import Column, check_unique
from .conditions import literal_type, literal_value
from .operators import Table
from .parser import parse
from .tree import Node


def load_definition(definition: Node) -> Table:
    name, names = definition.value
    columns = tuple(Column(name, column) for column in names)
    check_unique(columns)
    types = [None] * len(columns)
    rows = []
    for row in definition.children:
        if len(row.children) != len(columns):
            raise ExecutionError('Schema', f'{name}, line {row.token.line}: expected {len(columns)} values, got {len(row.children)}')
        for index, node in enumerate(row.children):
            value_type = literal_type(node)
            if types[index] is not None and types[index] != value_type:
                raise ExecutionError('Type', f'{name}.{names[index]} mixes numbers and strings at line {node.token.line}')
            types[index] = value_type
        rows.append(tuple(literal_value(node) for node in row.children))
    typed = tuple(replace(column, type=column_type) for column, column_type in zip(columns, types))
    return Table(typed, tuple(rows))


def load_relations(source: str) -> dict[str, Table]:
    tables = {}
    for definition in parse(source, relations=True).children:
        name = definition.value[0]
        if name in tables:
            raise ExecutionError('Schema', f'duplicate relation {name}')
        tables[name] = load_definition(definition)
    return tables
