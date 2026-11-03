"""What a running plan shares: the database, its pool of B frames, temp files.

Working memory is a list of frames reserved from the pool, so the pool's
own limit stops a plan from holding more than B pages. A Workspace only
admits as many tuples as would fill its pages, counting each tuple's
encoded size plus its 4 byte slot.
"""

import shutil
import struct
from pathlib import Path
from ..common.errors import ExecutionError
from ..common.schema import INT, VARCHAR, Column
from ..storage.page import PAGE_SIZE, USABLE_SPACE, SlottedPage

REAL = 'REAL'
SLOT_SIZE = 4
INT_FIELD = struct.Struct('<i')
WIDE_FIELD = struct.Struct('<q')   # SUM results, which can pass 32 bits
REAL_FIELD = struct.Struct('<d')
LENGTH_FIELD = struct.Struct('<H')


def is_wide(column: Column) -> bool:
    return column.type == INT and column.length == 8


def encode(values: tuple, columns: tuple[Column, ...]) -> bytes:
    parts = []
    for value, column in zip(values, columns):
        if column.type == REAL:
            parts.append(REAL_FIELD.pack(value))
        elif column.type == INT:
            parts.append((WIDE_FIELD if is_wide(column) else INT_FIELD).pack(value))
        else:
            data = value.encode('utf-8')
            parts.append(LENGTH_FIELD.pack(len(data)) + data)
    return b''.join(parts)


def decode(data: bytes, columns: tuple[Column, ...]) -> tuple:
    values = []
    offset = 0
    for column in columns:
        if column.type == VARCHAR:
            (length,) = LENGTH_FIELD.unpack_from(data, offset)
            offset += LENGTH_FIELD.size
            values.append(bytes(data[offset:offset + length]).decode('utf-8'))
            offset += length
            continue
        field = REAL_FIELD if column.type == REAL else WIDE_FIELD if is_wide(column) else INT_FIELD
        values.append(field.unpack_from(data, offset)[0])
        offset += field.size
    return tuple(values)


def encoded_size(values: tuple, columns: tuple[Column, ...]) -> int:
    size = 0
    for value, column in zip(values, columns):
        if column.type == VARCHAR:
            size += LENGTH_FIELD.size + len(value.encode('utf-8'))
        elif column.type == REAL or is_wide(column):
            size += 8
        else:
            size += 4
    return size


class Context:
    def __init__(self, db, frames: int) -> None:
        self.db = db
        self.pool = db.pool
        self.frames = frames
        self.temp_dir = Path(db.directory) / '.tmp'
        self.temp_dir.mkdir(exist_ok=True)
        self.temp_reads = 0
        self.temp_writes = 0
        self.file_number = 0

    def io(self) -> tuple[int, int]:
        return self.pool.reads + self.temp_reads, self.pool.writes + self.temp_writes

    def reserve(self, owner: object, count: int) -> list:
        if count > self.pool.free_frames():
            raise ExecutionError('memory', f'{owner} needs {count} frames but only '
                                 f'{self.pool.free_frames()} are free')
        return [self.pool.reserve_frame(owner) for _ in range(count)]

    def release(self, frames: list) -> None:
        for frame in frames:
            self.pool.release_frame(frame)

    def temp_file(self, owner: object) -> 'TempFile':
        self.file_number += 1
        return TempFile(self, owner, self.temp_dir / f'{self.file_number}.tmp')

    def leftovers(self) -> list[str]:
        problems = []
        if self.pool.pinned():
            problems.append(f'{self.pool.pinned()} frames still pinned')
        files = sorted(path.name for path in self.temp_dir.iterdir())
        if files:
            problems.append(f'temporary files left: {", ".join(files)}')
        return problems

    def remove_temp_dir(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class Workspace:
    """`count` reserved frames that admit tuples until their pages would be full."""

    def __init__(self, ctx: Context, owner: object, count: int, columns: tuple[Column, ...]) -> None:
        self.ctx = ctx
        self.frames = ctx.reserve(owner, count)
        self.columns = columns
        self.tuples = []
        self.page = 0
        self.used = 0

    def add(self, values: tuple) -> bool:
        """Keep `values` if they fit, else return False and keep nothing."""
        if not self.make_room(encoded_size(values, self.columns)):
            return False
        self.tuples.append(values)
        return True

    def make_room(self, size: int) -> bool:
        """Count `size` bytes plus a slot against the pages, if they fit."""
        needed = size + SLOT_SIZE
        if self.used + needed > USABLE_SPACE:
            if self.page + 1 >= len(self.frames) or needed > USABLE_SPACE:
                return False
            self.page += 1
            self.used = 0
        self.used += needed
        return True

    def pages_used(self) -> int:
        return self.page + 1 if self.used or self.page else 0

    def trim(self) -> None:
        """Give back the frames past the last page in use."""
        used = max(self.pages_used(), 1)
        self.ctx.release(self.frames[used:])
        self.frames = self.frames[:used]

    def clear(self) -> None:
        self.tuples = []
        self.page = 0
        self.used = 0

    def close(self) -> None:
        self.ctx.release(self.frames)
        self.frames = []
        self.tuples = []


class TempFile:
    """A file of slotted pages written front to back and read back in order."""

    def __init__(self, ctx: Context, owner: object, path: Path) -> None:
        self.ctx = ctx
        self.owner = owner
        self.path = path
        self.file = path.open('w+b')
        self.pages = 0
        self.tuples = 0
        self.writer = None
        self.borrowed = False

    def borrow(self, frame) -> 'TempFile':
        """Write through a frame the caller already holds, e.g. a sorted run's own memory."""
        self.writer = frame
        self.borrowed = True
        SlottedPage.format(frame.data, self.pages)
        return self

    def write(self, values: tuple, columns: tuple[Column, ...]) -> None:
        if self.writer is None:
            self.writer = self.ctx.reserve(self.owner, 1)[0]
            SlottedPage.format(self.writer.data, self.pages)
        record = encode(values, columns)
        page = SlottedPage(self.writer.data)
        if page.insert(record) is None:
            self.flush()
            SlottedPage.format(self.writer.data, self.pages)
            SlottedPage(self.writer.data).insert(record)
        self.tuples += 1

    def flush(self) -> None:
        self.file.seek(self.pages * PAGE_SIZE)
        self.file.write(bytes(self.writer.data))
        self.pages += 1
        self.ctx.temp_writes += 1

    def finish(self) -> 'TempFile':
        """Write the last page and give back the writer frame."""
        if self.writer is not None:
            if SlottedPage(self.writer.data).slot_count:
                self.flush()
            if not self.borrowed:
                self.ctx.release([self.writer])
            self.writer = None
        return self

    def read(self, columns: tuple[Column, ...]):
        """Yield every tuple, holding one frame while a page is being read."""
        frame = None
        try:
            for page_id in range(self.pages):
                if frame is None:
                    frame = self.ctx.reserve(self.owner, 1)[0]
                self.file.seek(page_id * PAGE_SIZE)
                frame.data[:] = self.file.read(PAGE_SIZE)
                self.ctx.temp_reads += 1
                for _, record in SlottedPage(frame.data).live_records():
                    yield decode(record, columns)
        finally:
            if frame is not None:
                self.ctx.release([frame])

    def delete(self) -> None:
        self.finish()
        self.file.close()
        self.path.unlink(missing_ok=True)
