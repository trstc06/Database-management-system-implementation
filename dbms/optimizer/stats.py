"""Statistics (Section 1): ANALYZE, and reading the results back.

ANALYZE counts tuples and record widths in one scan, then sorts each
column with the Module 2 external sort and walks the sorted values once
to get ndv, min, max and an equi-depth histogram. Nothing is sampled.

Histogram cuts fall at every n/k-th value, except that a run of equal
values is never split across two buckets. That keeps "find the bucket
holding v" well defined, at the cost of fewer than k buckets when one
value is very common.
"""

import math
from dataclasses import dataclass, field
from ..common.errors import DbError
from ..common.schema import INT, Column
from ..execution.base import END
from ..execution.context import Context
from ..execution.pipeline import Project, SeqScan
from ..execution.sort import Sort
from ..storage.catalog import BUCKET_KIND, STATS_KIND

DEFAULT_BUCKETS = 20
PAGE_SIZE = 4096
INT_WIDTH = 4
LENGTH_WIDTH = 2


@dataclass(frozen=True)
class Bucket:
    low: int | str
    high: int | str
    count: int
    ndv: int


@dataclass(frozen=True)
class ColumnStats:
    ndv: int
    width: float
    low: int | str | None
    high: int | str | None
    buckets: tuple[Bucket, ...] = ()


@dataclass(frozen=True)
class TableStats:
    name: str
    n_tuples: int
    n_pages: int
    width: float
    columns: dict = field(default_factory=dict)   # column name -> ColumnStats
    analyzed: bool = False


def value_width(value, column: Column) -> int:
    return INT_WIDTH if column.type == INT else LENGTH_WIDTH + len(value.encode('utf-8'))


def to_text(value) -> str:
    return '' if value is None else str(value)


def from_text(text: str, column: Column):
    return int(text) if column.type == INT else text


def histogram(values, n: int, k: int) -> tuple[list, int, object, object]:
    """Walk sorted values once: (buckets, ndv, min, max)."""
    buckets, ndv, low, high = [], 0, None, None
    start = count = bucket_ndv = position = 0
    current = previous = None
    for value in values:
        boundary = math.ceil((len(buckets) + 1) * n / k)
        if position > 0 and value != previous and position >= boundary:
            buckets.append(Bucket(start, previous, count, bucket_ndv))
            count = bucket_ndv = 0
        if count == 0:
            start = value
        if position == 0 or value != previous:
            ndv += 1
            bucket_ndv += 1
        if low is None:
            low = value
        high = previous = value
        count += 1
        position += 1
    if count:
        buckets.append(Bucket(start, previous, count, bucket_ndv))
    return buckets, ndv, low, high


def sorted_column(ctx: Context, table: str, column: Column):
    """Values of one column in order, from the external sort."""
    scan = SeqScan(ctx, table)
    name = f'{table}.{column.name}'
    root = Sort(ctx, [[name, 'asc']], Project(ctx, [name], scan))
    root.open()
    try:
        while (row := root.next()) is not END:
            yield row[0]
    finally:
        root.close()


def analyze(db, table_name: str, buckets: int = DEFAULT_BUCKETS) -> list[tuple]:
    """Compute exact statistics for one table and store them in the catalog."""
    if buckets < 1:
        raise DbError('schema', '--buckets must be at least 1')
    table = db.table(table_name)
    columns = table.columns
    n, totals = 0, [0] * len(columns)
    for row in table.rows():
        n += 1
        for i, (value, column) in enumerate(zip(row, columns)):
            totals[i] += value_width(value, column)
    if table.info.num_tuples != n or table.info.num_pages != table.heap.data_page_count():
        db.save_counts(table, n)
    ctx = Context(db, len(db.pool.frames))
    records = []
    try:
        for column, total in zip(columns, totals):
            cuts, ndv, low, high = histogram(sorted_column(ctx, table_name, column), n, buckets)
            records.append((STATS_KIND, table_name, column.name, ndv, total, to_text(low), to_text(high)))
            records.extend((BUCKET_KIND, table_name, column.name, index, to_text(b.low), to_text(b.high),
                            b.count, b.ndv) for index, b in enumerate(cuts))
    finally:
        ctx.remove_temp_dir()
    db.catalog.replace_stats(table_name, records)
    return records


def schema_width(column: Column) -> float:
    """A guess with no statistics: INT is 4 bytes, VARCHAR(n) half full."""
    return INT_WIDTH if column.type == INT else LENGTH_WIDTH + (column.length or 0) / 2


def table_stats(info, records: list[tuple]) -> TableStats:
    """Build TableStats from catalog records, or defaults if there are none."""
    by_name = {c.name: c for c in info.columns}
    columns = {}
    buckets = {}
    for values in records:
        if values[0] == BUCKET_KIND:
            _, _, name, _, low, high, count, ndv = values
            column = by_name[name]
            buckets.setdefault(name, []).append(Bucket(from_text(low, column), from_text(high, column), count, ndv))
    for values in records:
        if values[0] == STATS_KIND:
            _, _, name, ndv, total, low, high = values
            column = by_name[name]
            n = max(info.num_tuples, 1)
            present = ndv > 0   # an empty table has no min or max; '' is a real VARCHAR value
            columns[name] = ColumnStats(ndv, total / n, from_text(low, column) if present else None,
                                        from_text(high, column) if present else None, tuple(buckets.get(name, ())))
    if columns:
        width = sum(c.width for c in columns.values())
    else:
        width = sum(schema_width(c) for c in info.columns)
    return TableStats(info.name, info.num_tuples, info.num_pages, width, columns, analyzed=bool(columns))


def load_stats(db) -> dict:
    """{table: TableStats} for every table in the database."""
    catalog = db.catalog
    return {name: table_stats(catalog.get_table(name), catalog.stat_records(name)) for name in catalog.list_tables()}
