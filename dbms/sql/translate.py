"""Turn parsed SQL into a logical plan (Section 5).

Plans are built bottom to top in the canonical order: FROM, WHERE,
GROUP BY, HAVING, SELECT (project, then distinct), ORDER BY, LIMIT. Set
operators join whole blocks. Nothing is moved or optimized here.
"""

from ..common.schema import INT, MAX_VARCHAR, VARCHAR, Column
from ..ra.serialize import condition_to_text
from . import ast
from .grouping import Grouping
from .parser import parse
from .scope import (Entry, Output, bind_condition, column_operand, error, find_entry, literal_operand,
                    ref, resolve, table_columns, type_name)


def no_aggregates(condition, clause: str) -> None:
    if isinstance(condition, ast.Logical):
        for operand in condition.operands:
            no_aggregates(operand, clause)
        return
    for side in (condition.left, condition.right):
        if isinstance(side, ast.Aggregate):
            raise error('aggregate', f'{side.text()} cannot be used in {clause}, which runs before grouping; '
                        'put it in HAVING', side.pos)


def plain_operand(entries: list[Entry]):
    def operand(expr):
        if isinstance(expr, ast.Literal):
            return literal_operand(expr)
        return column_operand(resolve(expr, entries))
    return operand


class Translator:
    def __init__(self, catalog: dict) -> None:
        self.catalog = dict(catalog)

    def statement(self, node) -> dict:
        if isinstance(node, ast.CreateTable):
            return self.create(node)
        if isinstance(node, ast.Insert):
            return self.insert(node)
        return self.query(node)[0]

    # Queries ------------------------------------------------------------

    def query(self, node) -> tuple[dict, tuple[Column, ...]]:
        if isinstance(node, ast.SelectBlock):
            return self.block(node)
        left, left_columns = self.query(node.left)
        right, right_columns = self.query(node.right)
        label = node.op.upper().replace('_', ' ')
        if len(left_columns) != len(right_columns):
            raise error('schema', f'{label} needs the same number of columns on both sides, '
                        f'got {len(left_columns)} and {len(right_columns)}', node.pos)
        for position, (a, b) in enumerate(zip(left_columns, right_columns), start=1):
            if a.type != b.type:
                raise error('schema', f'{label} column {position} is {a.type} on the left ({ref(a)}) '
                            f'and {b.type} on the right ({ref(b)})', node.pos)
        return {'op': node.op, 'left': left, 'right': right}, left_columns

    def block(self, block: ast.SelectBlock) -> tuple[dict, tuple[Column, ...]]:
        plan, entries = self.from_clause(block.source)
        if block.where is not None:
            no_aggregates(block.where, 'WHERE')
            plan = select(plan, condition_to_text(bind_condition(block.where, plain_operand(entries))))
        items = self.expand(block.items, entries)
        grouping = None
        if block.group_by or any(isinstance(item.expr, ast.Aggregate) for item in items):
            grouping = Grouping(block, entries)
            outputs = [grouping.output(item) for item in items]
            having = grouping.having_condition()
            plan = grouping.node(plan)
            if having is not None:
                plan = select(plan, having)
        else:
            outputs = [Output(resolve(item.expr, entries), item.alias, item.pos) for item in items]
        check_unique_outputs(outputs)
        sort_keys, extras = self.order_keys(block, outputs, entries, grouping)
        plan = project(plan, [o.source for o in outputs] + extras, [o.alias for o in outputs] + [None] * len(extras))
        columns = tuple(o.column for o in outputs)
        if block.distinct and not (grouping and grouping.keys_all_in(outputs)):
            plan = {'op': 'distinct', 'child': plan}
        if sort_keys:
            plan = {'op': 'sort', 'keys': sort_keys, 'child': plan}
        if extras:
            plan = project(plan, list(columns), [None] * len(columns))
        if block.limit is not None:
            plan = {'op': 'limit', 'n': block.limit, 'child': plan}
        return plan, columns

    def expand(self, items, entries: list[Entry]) -> list[ast.SelectItem]:
        """SELECT * becomes every column of every FROM entry, fully qualified."""
        if not isinstance(items, ast.Star):
            return list(items)
        return [ast.SelectItem(ast.ColumnRef(entry.name, column.name, items.pos), None, items.pos)
                for entry in entries for column in entry.columns]

    def order_keys(self, block, outputs: list[Output], entries, grouping) -> tuple[list, list]:
        """Resolve ORDER BY against output names first, then the FROM scope.

        Returns the sort keys and any columns the project must carry only so
        the sort can see them (dropped again by a second project).
        """
        keys, extras = [], []
        for item in block.order_by:
            key = self.order_key(item.column, outputs, entries, grouping, block, extras)
            keys.append([key, 'desc' if item.descending else 'asc'])
        return keys, extras

    def order_key(self, column: ast.ColumnRef, outputs, entries, grouping, block, extras) -> str:
        if column.table is None:
            named = [o for o in outputs if o.column.qualifier is None and o.column.name == column.name]
            if named:
                return named[0].column.name
        found = resolve(column, entries)
        if grouping is not None:
            grouping.require_key(found, column.pos, 'ORDER BY')
        for output in outputs:
            if ref(output.source) == ref(found):
                return ref(output.column)
        if block.distinct:
            raise error('name', f'ORDER BY {ref(found)} must be in the select list when DISTINCT is used', column.pos)
        if found not in extras:
            extras.append(found)
        return ref(found)

    # FROM -------------------------------------------------------------

    def from_clause(self, source: ast.FromClause) -> tuple[dict, list[Entry]]:
        entries, plan = [], None
        for index, item in enumerate(source.refs):
            item_plan, entry = self.table_ref(item)
            if find_entry(entries, entry.name) is not None:
                raise error('name', f"'{entry.name}' appears twice in FROM; give one of them an alias", item.pos)
            entries.append(entry)
            if plan is None:
                plan = item_plan
            elif source.uses_join:
                condition = source.conditions[index - 1]
                no_aggregates(condition, 'ON')
                text = condition_to_text(bind_condition(condition, plain_operand(entries)))
                plan = {'op': 'join', 'condition': text, 'left': plan, 'right': item_plan}
            else:
                plan = {'op': 'cross', 'left': plan, 'right': item_plan}
        return plan, entries

    def table_ref(self, item) -> tuple[dict, Entry]:
        if isinstance(item, ast.SubqueryRef):
            plan, columns = self.query(item.query)
            seen = set()
            for column in columns:
                if column.name in seen:
                    raise error('schema', f"subquery {item.alias} has two columns named '{column.name}'; "
                                'use AS to rename one', item.pos)
                seen.add(column.name)
            renamed = tuple(Column(item.alias, c.name, c.type, c.length, show_qualifier=True) for c in columns)
            return {'op': 'rename', 'to': item.alias, 'child': plan}, Entry(item.alias, renamed)
        if item.name not in self.catalog:
            raise error('name', f"unknown table '{item.name}'", item.pos)
        plan = {'op': 'scan', 'table': item.name}
        name = item.alias or item.name
        if item.alias is not None:
            plan = {'op': 'rename', 'to': item.alias, 'child': plan}
        return plan, Entry(name, table_columns(name, self.catalog[item.name]))

    # CREATE and INSERT ------------------------------------------------

    def create(self, node: ast.CreateTable) -> dict:
        if node.table in self.catalog:
            raise error('schema', f"table '{node.table}' already exists", node.pos)
        seen = set()
        for column in node.columns:
            if column.name in seen:
                raise error('schema', f"column '{column.name}' appears twice in {node.table}", column.pos)
            seen.add(column.name)
            if column.type == VARCHAR and not 1 <= column.length <= MAX_VARCHAR:
                raise error('schema', f'VARCHAR length must be 1 to {MAX_VARCHAR}, got {column.length}', column.pos)
        self.catalog[node.table] = tuple(Column(node.table, c.name, c.type, c.length, show_qualifier=True)
                                         for c in node.columns)
        return {'op': 'create_table', 'table': node.table,
                'columns': [[c.name, c.type] + ([c.length] if c.type == VARCHAR else []) for c in node.columns]}

    def insert(self, node: ast.Insert) -> dict:
        if node.table not in self.catalog:
            raise error('name', f"unknown table '{node.table}'", node.pos)
        columns = self.catalog[node.table]
        if len(node.values) != len(columns):
            raise error('schema', f'{node.table} has {len(columns)} columns but {len(node.values)} values were given',
                        node.pos)
        for value, column in zip(node.values, columns):
            value_type = INT if isinstance(value.value, int) else VARCHAR
            if value_type != column.type:
                raise error('type', f'{ref(column)} is {type_name(column.type, column.length)}, '
                            f'got {value_type}', value.pos)
            if value_type == VARCHAR and len(value.value.encode('utf-8')) > column.length:
                raise error('schema', f'{ref(column)} holds at most {column.length} bytes', value.pos)
        return {'op': 'insert', 'table': node.table, 'values': [value.value for value in node.values]}


def select(child: dict, condition: str) -> dict:
    return {'op': 'select', 'condition': condition, 'child': child}


def project(child: dict, columns: list[Column], aliases: list) -> dict:
    node = {'op': 'project', 'columns': [ref(c) for c in columns], 'child': child}
    if any(alias is not None for alias in aliases):
        node = {'op': 'project', 'columns': node['columns'], 'aliases': aliases, 'child': child}
    return node


def check_unique_outputs(outputs: list[Output]) -> None:
    seen = set()
    for output in outputs:
        name = ref(output.column)
        if name in seen:
            raise error('schema', f"the select list has two columns named '{name}'; use AS to rename one", output.pos)
        seen.add(name)


def compile_sql(source: str, catalog: dict) -> list[dict]:
    translator = Translator(catalog)
    return [translator.statement(statement) for statement in parse(source)]
