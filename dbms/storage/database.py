"""A database directory: catalog.db plus one <table>.db file per table.

The catalog has its own small buffer pool, so the counters of the table
pool (the ones `storage scan` reports) count table pages only and do not
depend on how large the catalog is. Both pools are flushed on close.
"""

from pathlib import Path
from typing import Iterator
from ..common.errors import DbError
from ..common.schema import Column, check_unique
from .buffer import BufferPool
from .catalog import Catalog, TableInfo
from .disk import DiskManager
from .heap import HeapFile, Rid
from .record import check_type, decode_record, encode_record, minimum_length

CATALOG_FILE = 'catalog.db'
CATALOG_FRAMES = 4
RESERVED_NAMES = ('catalog',)


def check_identifier(name: str, what: str) -> None:
    """Letter or underscore, then letters, digits, underscores (also keeps file names safe)."""
    def name_start(c):
        return c == '_' or 'a' <= c <= 'z' or 'A' <= c <= 'Z'
    valid = name != '' and name_start(name[0]) and all(name_start(c) or '0' <= c <= '9' for c in name)
    if not valid:
        raise DbError('schema', f'{what} name {name!r} must be a letter or underscore followed by letters, digits or underscores')


class Table:
    """A table's heap file with its schema; values in and out are tuples."""

    def __init__(self, info: TableInfo, heap: HeapFile) -> None:
        self.info = info
        self.heap = heap

    @property
    def columns(self) -> tuple[Column, ...]:
        return self.info.columns

    def insert(self, values: tuple) -> Rid:
        return self.heap.insert(encode_record(values, self.columns))

    def get(self, rid: Rid) -> tuple:
        return decode_record(self.heap.get(rid), self.columns)

    def delete(self, rid: Rid) -> None:
        self.heap.delete(rid)

    def scan(self) -> Iterator[tuple[Rid, tuple]]:
        for rid, record in self.heap.scan():
            yield rid, decode_record(record, self.columns)

    def rows(self) -> Iterator[tuple]:
        return (row for _, row in self.scan())


class Database:
    def __init__(self, directory: Path, *, frames: int = 8, policy: str = 'lru', create: bool = False) -> None:
        self.directory = Path(directory)
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
        elif not (self.directory / CATALOG_FILE).exists():
            raise DbError('storage', f'{self.directory} is not a database directory (no {CATALOG_FILE})')
        self.pool = BufferPool(frames, policy)
        self.catalog_pool = BufferPool(CATALOG_FRAMES)
        self.disks = {}
        catalog_path = self.directory / CATALOG_FILE
        if catalog_path.exists():
            heap = HeapFile(self.catalog_pool, self.open_disk(CATALOG_FILE))
        else:
            heap = HeapFile.create(self.catalog_pool, self.open_disk(CATALOG_FILE, create=True))
        self.catalog = Catalog(heap)

    def __enter__(self) -> 'Database':
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open_disk(self, file: str, *, create: bool = False) -> DiskManager:
        if file not in self.disks:
            self.disks[file] = DiskManager(self.directory / file, create=create)
        return self.disks[file]

    def create_table(self, name: str, columns: tuple[Column, ...]) -> TableInfo:
        check_identifier(name, 'table')
        if name in RESERVED_NAMES:
            raise DbError('schema', f'table name {name} is reserved')
        if name in self.catalog.tables:
            raise DbError('schema', f'table {name} already exists')
        if not columns:
            raise DbError('schema', f'table {name} needs at least one column')
        for column in columns:
            check_identifier(column.name, 'column')
            check_type(column)
        check_unique(columns)
        file = f'{name}.db'
        HeapFile.create(self.pool, self.open_disk(file, create=True))
        return self.catalog.create_table(name, columns, file)

    def table(self, name: str) -> Table:
        info = self.catalog.get_table(name)
        heap = HeapFile(self.pool, self.open_disk(info.file), min_record=minimum_length(info.columns))
        return Table(info, heap)

    def save_counts(self, table: Table, num_tuples: int) -> TableInfo:
        info = self.catalog.update_counts(table.info.name, table.heap.data_page_count(), num_tuples)
        table.info = info
        return info

    def close(self) -> None:
        self.pool.flush_all()
        self.catalog_pool.flush_all()
        for disk in self.disks.values():
            disk.close()
        self.disks = {}
