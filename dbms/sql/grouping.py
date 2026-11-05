"""GROUP BY, aggregates and HAVING (Section 3.1).

An aggregate's output name is its AS alias, or else count_star for
COUNT(*) and function_table_column for the rest, like avg_Emp_salary.
The same aggregate written twice without an alias is computed once, so
HAVING COUNT(*) > 5 reuses the select list's COUNT(*) AS n.
"""

from dataclasses import dataclass
from ..common.schema import INT, Column
from ..ra.serialize import condition_to_text
from . import ast
from .scope import (REAL, Output, bind_condition, column_operand, error, literal_operand, ref, resolve)


@dataclass(frozen=True)
class AggregateOutput:
    function: str
    argument: Column | None
    column: Column

    def spec(self) -> list:
        return [self.function, '*' if self.argument is None else ref(self.argument), self.column.name]


def default_name(function: str, argument: Column | None) -> str:
    if argument is None:
        return 'count_star'
    return f'{function}_{ref(argument).replace(".", "_")}'


class Grouping:
    def __init__(self, block: ast.SelectBlock, entries: list) -> None:
        self.block = block
        self.entries = entries
        self.keys = []
        for column in block.group_by:
            found = resolve(column, entries)
            if found not in self.keys:
                self.keys.append(found)
        self.aggregates: list[AggregateOutput] = []

    def require_key(self, column: Column, pos: ast.Pos, clause: str) -> None:
        if column not in self.keys:
            raise error('aggregate', f'{ref(column)} in {clause} is not a grouping column; '
                        'add it to GROUP BY or use it inside an aggregate', pos)

    def aggregate(self, node: ast.Aggregate, alias: str | None) -> Column:
        argument = None
        if node.argument is not None:
            argument = resolve(node.argument, self.entries)
        elif node.function != 'count':
            raise error('aggregate', f'{node.function.upper()}(*) is not allowed; only COUNT(*)', node.pos)
        if node.function in ('sum', 'avg') and argument.type != INT:
            raise error('type', f'{node.function.upper()} needs an INT column, '
                        f'{ref(argument)} is {argument.type}', node.pos)
        for existing in self.aggregates:
            same = existing.function == node.function and existing.argument == argument
            if same and (alias is None or alias == existing.column.name):
                return existing.column
        name = alias or default_name(node.function, argument)
        if any(existing.column.name == name for existing in self.aggregates):
            raise error('schema', f"two aggregates are named '{name}'; use AS to rename one", node.pos)
        column = Column(None, name, *output_type(node.function, argument))
        self.aggregates.append(AggregateOutput(node.function, argument, column))
        return column

    def output(self, item: ast.SelectItem):
        if isinstance(item.expr, ast.Aggregate):
            return Output(self.aggregate(item.expr, item.alias), None, item.pos)
        column = resolve(item.expr, self.entries)
        self.require_key(column, item.pos, 'the select list')
        return Output(column, item.alias, item.pos)

    def having_condition(self) -> str | None:
        if self.block.having is None:
            return None
        return condition_to_text(bind_condition(self.block.having, self.having_operand))

    def having_operand(self, expr):
        if isinstance(expr, ast.Literal):
            return literal_operand(expr)
        if isinstance(expr, ast.Aggregate):
            return column_operand(self.aggregate(expr, None))
        named = [a.column for a in self.aggregates if expr.table is None and a.column.name == expr.name]
        if named and not any(c.name == expr.name for entry in self.entries for c in entry.columns):
            return column_operand(named[0])
        column = resolve(expr, self.entries)
        self.require_key(column, expr.pos, 'HAVING')
        return column_operand(column)

    def node(self, child: dict) -> dict:
        return {'op': 'group_by', 'keys': [ref(k) for k in self.keys],
                'aggregates': [a.spec() for a in self.aggregates], 'child': child}

    def keys_all_in(self, outputs: list) -> bool:
        """True when every grouping column is selected, so rows are already distinct."""
        selected = {ref(o.source) for o in outputs}
        return all(ref(k) in selected for k in self.keys)


def output_type(function: str, argument: Column | None) -> tuple:
    if function in ('count', 'sum'):
        return INT, None
    if function == 'avg':
        return REAL, None
    return argument.type, argument.length
