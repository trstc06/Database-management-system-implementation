"""Pipelined operators: seqscan, filter, project, limit and union_all."""

from dataclasses import replace
from ..common.errors import ExecutionError
from ..common.schema import check_unique, resolve
from ..ra.conditions import bind_condition
from ..ra.parser import parse_condition
from ..storage.record import decode_record
from .base import END, Operator


class SeqScan(Operator):
    name = 'seqscan'

    def __init__(self, ctx, table: str) -> None:
        super().__init__(ctx, [])
        self.table = ctx.db.table(table)
        self.table_name = table
        stored = self.table.columns
        self.columns = tuple(replace(column, qualifier=table, show_qualifier=True) for column in stored)
        self.stored = stored

    def label(self) -> str:
        return f'seqscan {self.table_name}'

    def streaming_frames(self) -> int:
        return 1

    def produce(self):
        if self.keep < 1:
            raise ExecutionError('memory', 'seqscan needs 1 frame')
        for _, record in self.table.heap.scan():
            yield decode_record(record, self.stored)


class Filter(Operator):
    name = 'filter'

    def __init__(self, ctx, condition: str, child: Operator) -> None:
        super().__init__(ctx, [child])
        self.condition = condition
        self.test = bind_condition(parse_condition(condition), child.columns)
        self.columns = child.columns

    def start(self) -> None:
        self.children[0].set_limits(self.hold, self.keep)
        self.children[0].open()

    def produce(self):
        for row in self.pull(self.children[0]):
            self.comparisons += 1
            if self.test(row):
                yield row

    def stop(self) -> None:
        self.children[0].close()


class Project(Operator):
    name = 'project'

    def __init__(self, ctx, names: list[str], child: Operator) -> None:
        super().__init__(ctx, [child])
        self.positions = [resolve(name, child.columns) for name in names]
        self.columns = tuple(child.columns[index] for index in self.positions)
        check_unique(self.columns)

    start = Filter.start
    stop = Filter.stop

    def produce(self):
        for row in self.pull(self.children[0]):
            yield tuple(row[index] for index in self.positions)


class Limit(Operator):
    name = 'limit'

    def __init__(self, ctx, count: int, child: Operator) -> None:
        super().__init__(ctx, [child])
        self.count = count
        self.columns = child.columns

    start = Filter.start
    stop = Filter.stop

    def produce(self):
        if self.count <= 0:
            return
        for row in self.pull(self.children[0]):
            yield row
            if self.tuples_out >= self.count:
                return


def check_compatible(name: str, left: Operator, right: Operator) -> None:
    if len(left.columns) != len(right.columns):
        raise ExecutionError('schema', f'{name} needs inputs with the same number of columns, '
                             f'got {len(left.columns)} and {len(right.columns)}')
    for position, (a, b) in enumerate(zip(left.columns, right.columns), start=1):
        if a.type != b.type:
            raise ExecutionError('schema', f'{name} column {position} is {a.type} on the left '
                                 f'and {b.type} on the right')


class UnionAll(Operator):
    """Left then right. The right input is opened only once the left is closed."""
    name = 'union_all'

    def __init__(self, ctx, left: Operator, right: Operator) -> None:
        super().__init__(ctx, [left, right])
        check_compatible(self.name, left, right)
        self.columns = left.columns

    def start(self) -> None:
        self.children[0].set_limits(self.hold, self.keep)
        self.children[0].open()

    def produce(self):
        left, right = self.children
        yield from self.pull(left)
        left.close()
        right.set_limits(self.keep, self.keep)
        right.open()
        yield from self.pull(right)

    def stop(self) -> None:
        for child in self.children:
            child.close()
