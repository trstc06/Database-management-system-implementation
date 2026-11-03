"""Slotted data page, edited in place inside a buffer frame.

    offset  size  field
         0     4  page_id      uint32
         4     2  slot_count   uint16, includes dead slots
         6     2  free_end     uint16, lowest byte offset used by record data
         8     4  slot 0       uint16 offset, uint16 length
        12     4  slot 1 ...
                  <-- free space -->
  free_end        record data, newest at the lowest offset, up to byte 4095

A slot with offset 0 is dead. Slots are never reused or renumbered, so a
rid (page id, slot number) stays valid. Deleting does not reclaim space;
there is no compaction.
"""

import struct
from typing import Iterator
from ..common.errors import DbError

PAGE_SIZE = 4096
HEADER = struct.Struct('<IHH')
SLOT = struct.Struct('<HH')
USABLE_SPACE = PAGE_SIZE - HEADER.size              # 4088
MAX_RECORD = USABLE_SPACE - SLOT.size               # 4084: an empty page must also fit the slot


class SlottedPage:
    def __init__(self, data: bytearray) -> None:
        self.data = data

    @classmethod
    def format(cls, data: bytearray, page_id: int) -> 'SlottedPage':
        data[:] = bytes(PAGE_SIZE)
        HEADER.pack_into(data, 0, page_id, 0, PAGE_SIZE)
        return cls(data)

    def header(self) -> tuple[int, int, int]:
        return HEADER.unpack_from(self.data, 0)

    @property
    def page_id(self) -> int:
        return self.header()[0]

    @property
    def slot_count(self) -> int:
        return self.header()[1]

    @property
    def free_end(self) -> int:
        return self.header()[2]

    def free_space(self) -> int:
        return self.free_end - (HEADER.size + SLOT.size * self.slot_count)

    def slot(self, slot_no: int) -> tuple[int, int]:
        return SLOT.unpack_from(self.data, HEADER.size + SLOT.size * slot_no)

    def insert(self, record: bytes) -> int | None:
        """Store a record and return its slot number, or None for NO_SPACE."""
        if self.free_space() < len(record) + SLOT.size:
            return None
        page_id, slot_count, free_end = self.header()
        free_end -= len(record)
        self.data[free_end:free_end + len(record)] = record
        SLOT.pack_into(self.data, HEADER.size + SLOT.size * slot_count, free_end, len(record))
        HEADER.pack_into(self.data, 0, page_id, slot_count + 1, free_end)
        return slot_count

    def check_live(self, slot_no: int) -> tuple[int, int]:
        if not 0 <= slot_no < self.slot_count:
            raise DbError('storage', f'rid ({self.page_id}, {slot_no}) does not exist; the page has {self.slot_count} slots')
        offset, length = self.slot(slot_no)
        if offset == 0:
            raise DbError('storage', f'rid ({self.page_id}, {slot_no}) refers to a deleted record')
        return offset, length

    def get(self, slot_no: int) -> bytes:
        offset, length = self.check_live(slot_no)
        return bytes(self.data[offset:offset + length])

    def overwrite(self, slot_no: int, record: bytes) -> None:
        """Replace a live record with one of the same length, keeping its rid."""
        offset, length = self.check_live(slot_no)
        if len(record) != length:
            raise DbError('storage', f'rid ({self.page_id}, {slot_no}) holds {length} bytes; cannot overwrite with {len(record)}')
        self.data[offset:offset + length] = record

    def delete(self, slot_no: int) -> None:
        self.check_live(slot_no)
        SLOT.pack_into(self.data, HEADER.size + SLOT.size * slot_no, 0, 0)

    def live_records(self) -> Iterator[tuple[int, bytes]]:
        for slot_no in range(self.slot_count):
            offset, length = self.slot(slot_no)
            if offset != 0:
                yield slot_no, bytes(self.data[offset:offset + length])
