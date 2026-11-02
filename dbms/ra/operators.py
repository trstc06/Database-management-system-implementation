"""Relational algebra operators on bags, detached from where tuples live.

Every operator takes sources and returns a lazy `Stream`. A source is
anything with `columns` and a `scan()` that starts a fresh pass over its
tuples, so the same operators run over an in-memory `Table`, a heap file,
or another operator. Names and types are checked when the operator is
built, before any tuple is read.

Projection keeps duplicates. Duplicate removal is its own operator,
`distinct`. `union_all` is bag concatenation; `union`, `intersect` and
`except_` use set semantics, so their output never repeats a tuple.
These in-memory operators are the reference semantics; Module 2 runs the
same algebra under a page budget.
"""

from dataclasses import dataclass, replace
from typing import Callable, Iterator, Protocol
from ..common.errors import ExecutionError
from ..common.schema import Column, check_unique, resolve
from .conditions import bind_condition
from .tree import Node


class Source(Protocol):
    columns: tuple[Column, ...]

    def scan(self) -> Iterator[tuple]: ...


@dataclass(frozen=True)
class Table:
    """Tuples held in memory, such as a relation file's contents."""
    columns: tuple[Column, ...]
    rows: tuple[tuple, ...]

    def scan(self) -> Iterator[tuple]:
        return iter(self.rows)


@dataclass(frozen=True)
class Stream:
    """A lazy operator result; every scan reruns the producer."""
    columns: tuple[Column, ...]
    producer: Callable[[], Iterator[tuple]]

    def scan(self) -> Iterator[tuple]:
        return self.producer()


@dataclass
class Statistics:
    join_pairs: int = 0
    selection_rows: int = 0
    wall_seconds: float = 0.0
    output_rows: int = 0


def materialize(source: Source) -> Table:
    return Table(source.columns, tuple(source.scan()))


def select(source: Source, condition: Node, stats: Statistics | None = None) -> Stream:
    matches = bind_condition(condition, source.columns)

    def rows():
        for row in source.scan():
            if stats is not None:
                stats.selection_rows += 1
            if matches(row):
                yield row
    return Stream(source.columns, rows)


def project(source: Source, names: tuple[str, ...]) -> Stream:
    """Keep the named columns in the given order. Duplicates stay."""
    indices = []
    for name in names:
        index = resolve(name, source.columns)
        if index in indices:
            raise ExecutionError('Schema', f'projection repeats column {name}')
        indices.append(index)
    columns = tuple(source.columns[i] for i in indices)
    return Stream(columns, lambda: (tuple(row[i] for i in indices) for row in source.scan()))


def distinct_rows(rows: Iterator[tuple]) -> Iterator[tuple]:
    """First occurrence of each tuple, in input order."""
    seen = set()
    for row in rows:
        if row not in seen:
            seen.add(row)
            yield row


def distinct(source: Source) -> Stream:
    return Stream(source.columns, lambda: distinct_rows(source.scan()))


def rename(source: Source, name: str) -> Stream:
    columns = tuple(replace(c, qualifier=name, show_qualifier=False) for c in source.columns)
    check_unique(columns)
    return Stream(columns, source.scan)


def product_columns(left: Source, right: Source) -> tuple[Column, ...]:
    columns = tuple(replace(c, show_qualifier=True) for c in left.columns + right.columns)
    check_unique(columns)
    return columns


def times(left: Source, right: Source) -> Stream:
    def rows():
        for a in left.scan():
            for b in right.scan():
                yield a + b
    return Stream(product_columns(left, right), rows)


def join(left: Source, right: Source, condition: Node, stats: Statistics | None = None) -> Stream:
    """Theta join: test every pair as it is formed, never storing the product."""
    columns = product_columns(left, right)
    matches = bind_condition(condition, columns)

    def rows():
        for a in left.scan():
            for b in right.scan():
                pair = a + b
                if stats is not None:
                    stats.join_pairs += 1
                if matches(pair):
                    yield pair
    return Stream(columns, rows)


def compatible(left: Source, right: Source) -> tuple[Column, ...]:
    """Same count, same column names in order, same types; output keeps the left's names."""
    if len(left.columns) != len(right.columns):
        raise ExecutionError('Schema', f'set operation requires the same number of columns; left has {len(left.columns)}, right has {len(right.columns)}')
    for index, (a, b) in enumerate(zip(left.columns, right.columns), start=1):
        if a.name != b.name:
            raise ExecutionError('Schema', f'set operation requires the same column names in the same order; column {index} is {a.label} on the left and {b.label} on the right')
        if a.type is not None and b.type is not None and a.type != b.type:
            raise ExecutionError('Schema', f'incompatible types for column {a.label}: {a.type} and {b.type}')
    # An empty input definition has no values from which to infer a type.
    return tuple(replace(a, type=a.type or b.type) for a, b in zip(left.columns, right.columns))


def union_all(left: Source, right: Source) -> Stream:
    def rows():
        yield from left.scan()
        yield from right.scan()
    return Stream(compatible(left, right), rows)


def union(left: Source, right: Source) -> Stream:
    combined = union_all(left, right)
    return Stream(combined.columns, lambda: distinct_rows(combined.scan()))


def intersect(left: Source, right: Source) -> Stream:
    columns = compatible(left, right)

    def rows():
        present = set(right.scan())
        return (row for row in distinct_rows(left.scan()) if row in present)
    return Stream(columns, rows)


def except_(left: Source, right: Source) -> Stream:
    columns = compatible(left, right)

    def rows():
        present = set(right.scan())
        return (row for row in distinct_rows(left.scan()) if row not in present)
    return Stream(columns, rows)
