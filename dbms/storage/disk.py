"""Disk managers: the only code in the system that touches a file.

A `DiskManager` reads and writes fixed 4096 byte pages of one file by page
id and counts every call. The file has no header outside its pages.
`NullDisk` stands in for a file when replaying a trace: every page reads as
zeros and nothing is stored.
"""

from pathlib import Path
from ..common.errors import DbError
from .page import PAGE_SIZE


class DiskManager:
    def __init__(self, path: Path, *, create: bool = False) -> None:
        self.path = Path(path)
        if create:
            if self.path.exists():
                raise DbError('storage', f'{self.path.name} already exists')
            self.path.touch()
        elif not self.path.exists():
            raise DbError('storage', f'{self.path.name} does not exist')
        self.file = self.path.open('r+b')
        size = self.path.stat().st_size
        if size % PAGE_SIZE:
            raise DbError('storage', f'{self.path.name} is {size} bytes, not a whole number of pages')
        self.allocated = size // PAGE_SIZE
        self.reads = 0
        self.writes = 0

    def num_pages(self) -> int:
        return self.allocated

    def allocate_page(self) -> int:
        """Reserve the next page id. The page reaches the file when written."""
        self.allocated += 1
        return self.allocated - 1

    def check(self, page_id: int) -> None:
        if not 0 <= page_id < self.allocated:
            raise DbError('storage', f'page {page_id} is outside {self.path.name}, which has {self.allocated} pages')

    def read_page(self, page_id: int) -> bytes:
        self.check(page_id)
        self.reads += 1
        self.file.seek(page_id * PAGE_SIZE)
        data = self.file.read(PAGE_SIZE)
        # An allocated page that was never written reads as zeros.
        return data + bytes(PAGE_SIZE - len(data))

    def write_page(self, page_id: int, data: bytes) -> None:
        self.check(page_id)
        if len(data) != PAGE_SIZE:
            raise DbError('storage', f'a page write must be {PAGE_SIZE} bytes, got {len(data)}')
        self.writes += 1
        self.file.seek(page_id * PAGE_SIZE)
        self.file.write(data)

    def close(self) -> None:
        self.file.close()


class NullDisk:
    """A file of unlimited zero pages, for buffer pool traces."""

    def __init__(self) -> None:
        self.reads = 0
        self.writes = 0

    def num_pages(self) -> int:
        return 0

    def read_page(self, page_id: int) -> bytes:
        self.reads += 1
        return bytes(PAGE_SIZE)

    def write_page(self, page_id: int, data: bytes) -> None:
        self.writes += 1

    def close(self) -> None:
        pass
