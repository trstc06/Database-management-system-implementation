"""The catalog: table metadata stored as records in catalog.db.

catalog.db is an ordinary heap file, read and written through a buffer
pool. Each catalog record starts with an INT kind tag that says which
schema the rest of the record uses, so new kinds can be added without
changing existing ones.

    kind 1  TABLE    name, file, num_pages, num_tuples
    kind 2  COLUMN   table, position, name, type, length (0 for INT)
    kind 3  STATS    table, column, ndv, total_bytes, min, max
    kind 4  BUCKET   table, column, index, low, high, count, ndv

Statistics come from ANALYZE (Module 4). min, max, low and high are kept
as text whatever the column type; the optimizer turns them back into
values using the column's type.

num_pages counts data pages only, not directory pages.
"""

import struct
from dataclasses import dataclass, replace
from ..common.errors import DbError
from ..common.schema import INT, MAX_VARCHAR, VARCHAR, Column
from .heap import HeapFile
from .record import decode_record, encode_record

TABLE_KIND, COLUMN_KIND, STATS_KIND, BUCKET_KIND = 1, 2, 3, 4
KIND_FIELD = struct.Struct('<i')


def catalog_columns(*fields: tuple[str, str]) -> tuple[Column, ...]:
    return tuple(Column('catalog', name, kind, MAX_VARCHAR if kind == VARCHAR else None)
                 for name, kind in fields)


RECORD_SCHEMAS = {
    TABLE_KIND: catalog_columns(('kind', INT), ('name', VARCHAR), ('file', VARCHAR),
                                ('num_pages', INT), ('num_tuples', INT)),
    COLUMN_KIND: catalog_columns(('kind', INT), ('table', VARCHAR), ('position', INT),
                                 ('name', VARCHAR), ('type', VARCHAR), ('length', INT)),
    STATS_KIND: catalog_columns(('kind', INT), ('table', VARCHAR), ('column', VARCHAR), ('ndv', INT),
                                ('total_bytes', INT), ('min', VARCHAR), ('max', VARCHAR)),
    BUCKET_KIND: catalog_columns(('kind', INT), ('table', VARCHAR), ('column', VARCHAR), ('index', INT),
                                 ('low', VARCHAR), ('high', VARCHAR), ('count', INT), ('ndv', INT)),
}
STATS_KINDS = (STATS_KIND, BUCKET_KIND)


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: tuple[Column, ...]
    file: str
    num_pages: int = 0
    num_tuples: int = 0
    first_page: int = 0


def encode_table(info: TableInfo) -> bytes:
    return encode_record((TABLE_KIND, info.name, info.file, info.num_pages, info.num_tuples),
                         RECORD_SCHEMAS[TABLE_KIND])


def encode_column(table: str, position: int, column: Column) -> bytes:
    values = (COLUMN_KIND, table, position, column.name, column.type, column.length or 0)
    return encode_record(values, RECORD_SCHEMAS[COLUMN_KIND])


def decode_catalog_record(record: bytes) -> tuple:
    (kind,) = KIND_FIELD.unpack_from(record, 0)
    if kind not in RECORD_SCHEMAS:
        raise DbError('storage', f'catalog record has unknown kind {kind}')
    return decode_record(record, RECORD_SCHEMAS[kind])


class Catalog:
    def __init__(self, heap: HeapFile) -> None:
        self.heap = heap
        self.tables = {}       # name -> TableInfo, in creation order
        self.table_rids = {}   # name -> rid of its TABLE record
        self.stats = {}        # table -> [(rid, values)] of its STATS and BUCKET records
        self.load()

    def load(self) -> None:
        columns = {}
        for rid, record in self.heap.scan():
            values = decode_catalog_record(record)
            if values[0] == TABLE_KIND:
                _, name, file, num_pages, num_tuples = values
                self.tables[name] = TableInfo(name, (), file, num_pages, num_tuples)
                self.table_rids[name] = rid
            elif values[0] in STATS_KINDS:
                self.stats.setdefault(values[1], []).append((rid, values))
            else:
                _, table, position, name, kind, length = values
                columns.setdefault(table, []).append(
                    (position, Column(table, name, kind, length if kind == VARCHAR else None)))
        for name, info in self.tables.items():
            ordered = tuple(column for _, column in sorted(columns.get(name, [])))
            self.tables[name] = replace(info, columns=ordered)

    def create_table(self, name: str, columns: tuple[Column, ...], file: str) -> TableInfo:
        if name in self.tables:
            raise DbError('schema', f'table {name} already exists')
        info = TableInfo(name, columns, file)
        self.table_rids[name] = self.heap.insert(encode_table(info))
        for position, column in enumerate(columns):
            self.heap.insert(encode_column(name, position, column))
        self.tables[name] = info
        return info

    def get_table(self, name: str) -> TableInfo:
        if name not in self.tables:
            raise DbError('name', f'unknown table {name}')
        return self.tables[name]

    def list_tables(self) -> list[str]:
        return list(self.tables)

    def stat_records(self, table: str) -> list[tuple]:
        return [values for _, values in self.stats.get(table, [])]

    def replace_stats(self, table: str, records: list[tuple]) -> bool:
        """Store new STATS and BUCKET records. Identical ones are left alone,
        so a second ANALYZE of unchanged data writes nothing. Returns True if
        anything changed."""
        self.get_table(table)
        if self.stat_records(table) == records:
            return False
        for rid, _ in self.stats.get(table, []):
            self.heap.delete(rid)
        self.stats[table] = [(self.heap.insert(encode_record(values, RECORD_SCHEMAS[values[0]])), values)
                             for values in records]
        return True

    def update_counts(self, name: str, num_pages: int, num_tuples: int) -> TableInfo:
        """Rewrite the TABLE record in place. The counts are fixed-width INTs
        and the names do not change, so the record keeps its length and its
        rid, and the catalog keeps its order."""
        info = replace(self.get_table(name), num_pages=num_pages, num_tuples=num_tuples)
        self.heap.overwrite(self.table_rids[name], encode_table(info))
        self.tables[name] = info
        return info
