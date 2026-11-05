"""A small catalog in the shape of the Module 3 examples."""

from dbms.common.errors import DbError
from dbms.sql.scope import json_column
from dbms.sql.translate import compile_sql

TABLES = {
    'Emp': [['eid', 'INT'], ['name', 'VARCHAR', 20], ['age', 'INT'], ['did', 'INT'],
            ['salary', 'INT'], ['mgr', 'INT'], ['city', 'VARCHAR', 20]],
    'Dept': [['did', 'INT'], ['title', 'VARCHAR', 20], ['budget', 'INT'], ['city', 'VARCHAR', 20]],
    'Proj': [['pid', 'INT'], ['did', 'INT'], ['lead', 'INT']],
    'Site': [['sid', 'INT'], ['pid', 'INT'], ['city', 'VARCHAR', 20]],
}
CATALOG = {name: tuple(json_column(name, c) for c in columns) for name, columns in TABLES.items()}


def plan(sql: str) -> dict:
    return compile_sql(sql, CATALOG)[0]


def failure(sql: str) -> DbError:
    try:
        compile_sql(sql, CATALOG)
    except DbError as error:
        return error
    raise AssertionError(f'expected an error for {sql!r}')


def scan(table: str, alias: str | None = None) -> dict:
    node = {'op': 'scan', 'table': table}
    return {'op': 'rename', 'to': alias, 'child': node} if alias else node


def ops(node: dict) -> list[str]:
    """Every op name in the plan, top down, left before right."""
    out = [node['op']]
    for key in ('child', 'left', 'right'):
        if key in node:
            out.extend(ops(node[key]))
    return out


def columns_in(node: dict) -> list[str]:
    """Every column name a plan mentions in columns, keys and aggregates."""
    names = list(node.get('columns', []))
    if node['op'] == 'group_by':
        names += node['keys'] + [a[1] for a in node['aggregates'] if a[1] != '*']
    if node['op'] == 'sort':
        names += [k[0] for k in node['keys']]
    for key in ('child', 'left', 'right'):
        if key in node:
            names += columns_in(node[key])
    return names
