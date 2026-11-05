"""Name resolution (Section 3) and condition checking (Section 3.2).

A scope is the list of FROM entries, each a name (alias or table name)
with its columns. Columns are common.schema.Column values. A column made
by the query itself, like an aggregate or an AS name, has qualifier None.
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from ..common.errors import DbError
from ..common.schema import INT, VARCHAR, Column
from ..ra.tree import Node
from ..storage.database import Database
from . import ast

REAL = 'REAL'
NUMERIC = (INT, REAL)
COMPARISON_KINDS = {'=': 'Eq', '<>': 'Ne', '<': 'Lt', '<=': 'Le', '>': 'Gt', '>=': 'Ge'}


def error(category: str, message: str, pos: ast.Pos) -> DbError:
    return DbError(category, message, pos.line, pos.column)


def ref(column: Column) -> str:
    """How a column is named in a plan: Emp.age, or n for a computed column."""
    return column.qualified_name if column.qualifier else column.name


def type_name(column_type: str, length: int | None = None) -> str:
    return f'VARCHAR({length})' if column_type == VARCHAR and length else column_type


@dataclass(frozen=True)
class Entry:
    name: str
    columns: tuple[Column, ...]


@dataclass(frozen=True)
class Output:
    """One select list column: what the project reads and what it is called after."""
    source: Column
    alias: str | None
    pos: ast.Pos

    @property
    def column(self) -> Column:
        if self.alias is None:
            return self.source
        return Column(None, self.alias, self.source.type, self.source.length)


def find_entry(entries: list[Entry], name: str) -> Entry | None:
    return next((entry for entry in entries if entry.name == name), None)


def resolve(column: ast.ColumnRef, entries: list[Entry]) -> Column:
    if column.table is not None:
        entry = find_entry(entries, column.table)
        if entry is None:
            raise error('name', f"unknown table or alias '{column.table}' in {column.text()}", column.pos)
        found = [c for c in entry.columns if c.name == column.name]
        if not found:
            raise error('name', f"'{column.table}' has no column '{column.name}'", column.pos)
        return found[0]
    candidates = [c for entry in entries for c in entry.columns if c.name == column.name]
    if not candidates:
        raise error('name', f"unknown column '{column.name}'", column.pos)
    if len(candidates) > 1:
        names = ', '.join(ref(c) for c in candidates)
        raise error('ambiguity', f"column '{column.name}' is ambiguous; it could be {names}", column.pos)
    return candidates[0]


def compatible(a: str, b: str) -> bool:
    return a == b or (a in NUMERIC and b in NUMERIC)


def bind_condition(node, operand) -> Node:
    """Check a condition and turn it into the ra tree that prints as plan text.

    `operand(expr)` resolves one side of a comparison to (Node, type, label).
    """
    if isinstance(node, ast.Logical):
        kind = node.op.capitalize()
        return Node(kind, children=tuple(bind_condition(child, operand) for child in node.operands))
    left, left_type, left_label = operand(node.left)
    right, right_type, right_label = operand(node.right)
    if not compatible(left_type, right_type):
        raise error('type', f'cannot compare {left_label} ({left_type}) with {right_label} ({right_type})', node.pos)
    return Node(COMPARISON_KINDS[node.op], children=(left, right))


def literal_operand(literal: ast.Literal) -> tuple[Node, str, str]:
    if isinstance(literal.value, int):
        return Node('Num', str(literal.value)), INT, str(literal.value)
    return Node('Str', literal.value), VARCHAR, "'" + literal.value + "'"


def column_operand(column: Column) -> tuple[Node, str, str]:
    return Node('Attr', ref(column)), column.type, ref(column)


# Catalogs --------------------------------------------------------------

def table_columns(name: str, columns) -> tuple[Column, ...]:
    return tuple(replace(c, qualifier=name, show_qualifier=True) for c in columns)


def parse_type(text: str, where: str) -> tuple[str, int | None]:
    upper = text.strip().upper()
    if upper == INT:
        return INT, None
    if upper.startswith('VARCHAR(') and upper.endswith(')') and upper[8:-1].isdigit():
        return VARCHAR, int(upper[8:-1])
    raise DbError('schema', f'{where} has type {text!r}; use INT or VARCHAR(n)')


def json_column(table: str, item) -> Column:
    """Accept ["a", "INT"], ["b", "VARCHAR", 20], {"name": .., "type": .., "length": ..} or "VARCHAR(20)"."""
    if isinstance(item, dict):
        name, kind, length = item.get('name'), item.get('type', ''), item.get('length')
    elif isinstance(item, list) and len(item) in (2, 3):
        name, kind, length = item[0], item[1], item[2] if len(item) == 3 else None
    else:
        raise DbError('schema', f'cannot read column {item!r} of table {table}')
    if str(kind).upper() == VARCHAR and isinstance(length, int):
        return Column(table, str(name), VARCHAR, length, show_qualifier=True)
    column_type, parsed_length = parse_type(str(kind), f'{table}.{name}')
    return Column(table, str(name), column_type, length or parsed_length, show_qualifier=True)


def load_json_catalog(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    tables = data.get('tables', data) if isinstance(data, dict) else data
    if isinstance(tables, dict):
        items = [(name, value.get('columns', value) if isinstance(value, dict) else value)
                 for name, value in tables.items()]
    else:
        items = [(table['name'], table['columns']) for table in tables]
    return {name: tuple(json_column(name, c) for c in columns) for name, columns in items}


def load_catalog(location: str | None) -> dict:
    """{table: columns} from a database folder or a JSON catalog file."""
    if location is None:
        return {}
    path = Path(location)
    if path.is_file():
        return load_json_catalog(path)
    with Database(path) as db:
        return {name: table_columns(name, db.catalog.get_table(name).columns)
                for name in db.catalog.list_tables()}
