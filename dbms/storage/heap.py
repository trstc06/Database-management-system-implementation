"""Heap file: a linked list of directory pages over slotted data pages.

Directory page layout (Section 4). Page 0 of every file is the first one.

    offset  size  field
         0     4  page_id         uint32
         4     4  next_dir_page   uint32, 0xFFFFFFFF if none
         8     2  entry_count     uint16
        10     6  entry 0         uint32 data_page_id, uint16 free_bytes
        16     6  entry 1 ...     up to 681 entries

Every page access goes through the buffer pool. A scan copies one
directory page's list of data page ids, unpins it, and then visits the
data pages one at a time, so it never holds more than one pinned page.
"""

import struct
from typing import Iterator
from ..common.errors import DbError
from .buffer import BufferPool
from .page import MAX_RECORD, PAGE_SIZE, SlottedPage

DIRECTORY_HEADER = struct.Struct('<IIH')
ENTRY = struct.Struct('<IH')
DIRECTORY_CAPACITY = (PAGE_SIZE - DIRECTORY_HEADER.size) // ENTRY.size  # 681
NO_PAGE = 0xFFFFFFFF
FIRST_DIRECTORY = 0

Rid = tuple[int, int]


class DirectoryPage:
    def __init__(self, data: bytearray) -> None:
        self.data = data

    @classmethod
    def format(cls, data: bytearray, page_id: int) -> 'DirectoryPage':
        data[:] = bytes(PAGE_SIZE)
        DIRECTORY_HEADER.pack_into(data, 0, page_id, NO_PAGE, 0)
        return cls(data)

    def header(self) -> tuple[int, int, int]:
        return DIRECTORY_HEADER.unpack_from(self.data, 0)

    @property
    def next_page(self) -> int:
        return self.header()[1]

    @property
    def entry_count(self) -> int:
        return self.header()[2]

    def entries(self) -> list[tuple[int, int]]:
        count = self.entry_count
        end = DIRECTORY_HEADER.size + ENTRY.size * count
        return list(ENTRY.iter_unpack(bytes(self.data[DIRECTORY_HEADER.size:end])))

    def set_entry(self, index: int, page_id: int, free_bytes: int) -> None:
        ENTRY.pack_into(self.data, DIRECTORY_HEADER.size + ENTRY.size * index, page_id, free_bytes)

    def append(self, page_id: int, free_bytes: int) -> None:
        own_id, next_page, count = self.header()
        self.set_entry(count, page_id, free_bytes)
        DIRECTORY_HEADER.pack_into(self.data, 0, own_id, next_page, count + 1)

    def link(self, next_page: int) -> None:
        own_id, _, count = self.header()
        DIRECTORY_HEADER.pack_into(self.data, 0, own_id, next_page, count)


class HeapFile:
    def __init__(self, pool: BufferPool, disk, *, min_record: int = 1) -> None:
        self.pool = pool
        self.disk = disk
        # Free space only shrinks (no compaction), so a leading run of pages
        # too full for even the shortest record can be skipped by insert.
        # This changes no counter: every directory page is still fetched.
        self.min_need = min_record + 4
        self.full_prefix = 0

    @classmethod
    def create(cls, pool: BufferPool, disk, **options) -> 'HeapFile':
        frame = pool.new_page(disk)
        if frame.page_id != FIRST_DIRECTORY:
            raise DbError('storage', f'{disk.path.name} is not empty')
        DirectoryPage.format(frame.data, FIRST_DIRECTORY)
        pool.unpin_page(disk, FIRST_DIRECTORY, True)
        return cls(pool, disk, **options)

    def fetch_directory(self, page_id: int) -> DirectoryPage:
        return DirectoryPage(self.pool.fetch_page(self.disk, page_id).data)

    def directory_ids(self) -> list[int]:
        """Page ids of the directory chain, fetching each page once."""
        ids = []
        page_id = FIRST_DIRECTORY
        while page_id != NO_PAGE:
            ids.append(page_id)
            following = self.fetch_directory(page_id).next_page
            self.pool.unpin_page(self.disk, page_id, False)
            page_id = following
        return ids

    def insert(self, record: bytes) -> Rid:
        """First fit over the directory; a new data page when none has room."""
        if len(record) > MAX_RECORD:
            raise DbError('storage', f'a {len(record)} byte record is longer than the {MAX_RECORD} bytes a page can hold')
        need = len(record) + 4
        position = 0      # entries seen in earlier directory pages
        page_id = FIRST_DIRECTORY
        while True:
            directory = self.fetch_directory(page_id)
            entries = directory.entries()
            start = max(0, self.full_prefix - position)
            for index in range(start, len(entries)):
                data_id, free = entries[index]
                if free < self.min_need and position + index == self.full_prefix:
                    self.full_prefix += 1
                if free >= need:
                    return self.insert_into(page_id, directory, index, data_id, record)
            following = directory.next_page
            if following == NO_PAGE:
                return self.insert_new_page(page_id, directory, record)
            self.pool.unpin_page(self.disk, page_id, False)
            position += len(entries)
            page_id = following

    def insert_into(self, directory_id: int, directory: DirectoryPage, index: int,
                    data_id: int, record: bytes) -> Rid:
        page = SlottedPage(self.pool.fetch_page(self.disk, data_id).data)
        slot_no = page.insert(record)
        directory.set_entry(index, data_id, page.free_space())
        self.pool.unpin_page(self.disk, data_id, True)
        self.pool.unpin_page(self.disk, directory_id, True)
        return data_id, slot_no

    def insert_new_page(self, directory_id: int, directory: DirectoryPage, record: bytes) -> Rid:
        """Called with the last directory page pinned."""
        if directory.entry_count == DIRECTORY_CAPACITY:
            frame = self.pool.new_page(self.disk)
            directory.link(frame.page_id)
            self.pool.unpin_page(self.disk, directory_id, True)
            directory_id = frame.page_id
            directory = DirectoryPage.format(frame.data, directory_id)
        frame = self.pool.new_page(self.disk)
        page = SlottedPage.format(frame.data, frame.page_id)
        slot_no = page.insert(record)
        directory.append(frame.page_id, page.free_space())
        self.pool.unpin_page(self.disk, frame.page_id, True)
        self.pool.unpin_page(self.disk, directory_id, True)
        return frame.page_id, slot_no

    def get(self, rid: Rid) -> bytes:
        page_id, slot_no = rid
        frame = self.pool.fetch_page(self.disk, page_id)
        try:
            return SlottedPage(frame.data).get(slot_no)
        finally:
            self.pool.unpin_page(self.disk, page_id, False)

    def overwrite(self, rid: Rid, record: bytes) -> None:
        """Replace a record in place with one of the same length."""
        page_id, slot_no = rid
        frame = self.pool.fetch_page(self.disk, page_id)
        try:
            SlottedPage(frame.data).overwrite(slot_no, record)
        except DbError:
            self.pool.unpin_page(self.disk, page_id, False)
            raise
        self.pool.unpin_page(self.disk, page_id, True)

    def delete(self, rid: Rid) -> None:
        """Mark the slot dead. Without compaction its bytes stay used, so the
        directory's free_bytes for the page is unchanged and is not rewritten."""
        page_id, slot_no = rid
        frame = self.pool.fetch_page(self.disk, page_id)
        try:
            SlottedPage(frame.data).delete(slot_no)
        except DbError:
            self.pool.unpin_page(self.disk, page_id, False)
            raise
        self.pool.unpin_page(self.disk, page_id, True)

    def data_page_ids(self, directory_id: int) -> tuple[list[int], int]:
        """Copy one directory page's data page ids and its next pointer, then unpin it."""
        directory = self.fetch_directory(directory_id)
        ids = [data_id for data_id, _ in directory.entries()]
        following = directory.next_page
        self.pool.unpin_page(self.disk, directory_id, False)
        return ids, following

    def scan(self) -> Iterator[tuple[Rid, bytes]]:
        """Every live record in heap order, one data page pinned at a time."""
        directory_id = FIRST_DIRECTORY
        while directory_id != NO_PAGE:
            ids, directory_id = self.data_page_ids(directory_id)
            for data_id in ids:
                page = SlottedPage(self.pool.fetch_page(self.disk, data_id).data)
                try:
                    for slot_no, record in page.live_records():
                        yield (data_id, slot_no), record
                finally:
                    # Also runs when the consumer stops early, e.g. under LIMIT.
                    self.pool.unpin_page(self.disk, data_id, False)

    def data_page_count(self) -> int:
        count = 0
        directory_id = FIRST_DIRECTORY
        while directory_id != NO_PAGE:
            ids, directory_id = self.data_page_ids(directory_id)
            count += len(ids)
        return count
