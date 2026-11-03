"""Buffer pool: B frames, pinning, dirty tracking and a replacement policy.

One pool can serve several files. A page is identified by its disk manager
and its page id within that file, so the page table key is (disk, page_id).

Counting rules (Section 5): a hit is a fetch of a resident page, a miss is
a fetch that read from disk, an eviction is a miss that replaced a resident
page, and reads and writes count calls this pool makes to a disk manager,
whether a write comes from an eviction or from flush_all. `new_page` is
neither a hit nor a miss; if it has to write out a dirty victim, that write
counts.
"""

from dataclasses import dataclass, field
from ..common.errors import DbError
from .page import PAGE_SIZE
from .policies import POLICIES


@dataclass
class Frame:
    index: int
    data: bytearray = field(default_factory=lambda: bytearray(PAGE_SIZE))
    key: tuple | None = None   # (disk, page_id) of the resident page
    pin_count: int = 0
    dirty: bool = False

    @property
    def page_id(self) -> int:
        return self.key[1]


class BufferPool:
    def __init__(self, frame_count: int, policy: str = 'lru') -> None:
        if frame_count < 1:
            raise DbError('storage', 'the buffer pool needs at least one frame')
        if policy not in POLICIES:
            raise DbError('storage', f"unknown policy {policy!r}; use 'lru' or 'clock'")
        self.frames = [Frame(index) for index in range(frame_count)]
        self.policy = POLICIES[policy](frame_count)
        self.page_table = {}
        self.hits = self.misses = self.evictions = self.reads = self.writes = 0
        self.peak_pinned = 0

    def pinned(self) -> int:
        return sum(1 for frame in self.frames if frame.pin_count > 0)

    def note_pin(self) -> None:
        self.peak_pinned = max(self.peak_pinned, self.pinned())

    def take_victim(self) -> Frame:
        index = self.policy.choose_victim(self.frames)
        if index is None:
            raise DbError('storage', 'no unpinned frame available')
        frame = self.frames[index]
        if frame.key is not None:
            if frame.dirty:
                disk, page_id = frame.key
                disk.write_page(page_id, bytes(frame.data))
                self.writes += 1
            del self.page_table[frame.key]
        return frame

    def install(self, frame: Frame, key: tuple, dirty: bool) -> Frame:
        frame.key = key
        frame.pin_count = 1
        frame.dirty = dirty
        self.page_table[key] = frame.index
        self.policy.on_access(frame.index)
        self.note_pin()
        return frame

    def fetch_page(self, disk, page_id: int) -> Frame:
        key = (disk, page_id)
        if key in self.page_table:
            frame = self.frames[self.page_table[key]]
            frame.pin_count += 1
            self.hits += 1
            self.policy.on_access(frame.index)
            self.note_pin()
            return frame
        frame = self.take_victim()
        replaced = frame.key is not None
        frame.key = None
        frame.data[:] = disk.read_page(page_id)
        self.reads += 1
        self.misses += 1
        if replaced:
            self.evictions += 1
        return self.install(frame, key, dirty=False)

    def new_page(self, disk) -> Frame:
        """Allocate a page in `disk`; returned zero filled, pinned and dirty."""
        frame = self.take_victim()
        frame.key = None
        frame.data[:] = bytes(PAGE_SIZE)
        return self.install(frame, (disk, disk.allocate_page()), dirty=True)

    def unpin_page(self, disk, page_id: int, is_dirty: bool) -> None:
        index = self.page_table.get((disk, page_id))
        if index is None:
            raise DbError('storage', f'cannot unpin page {page_id}: it is not resident')
        frame = self.frames[index]
        if frame.pin_count == 0:
            raise DbError('storage', f'cannot unpin page {page_id}: its pin count is already 0')
        frame.dirty = frame.dirty or is_dirty
        frame.pin_count -= 1

    def flush_all(self) -> None:
        for frame in self.frames:
            if frame.key is not None and frame.dirty:
                disk, page_id = frame.key
                disk.write_page(page_id, bytes(frame.data))
                self.writes += 1
                frame.dirty = False

    def stats(self) -> dict:
        return {'hits': self.hits, 'misses': self.misses, 'evictions': self.evictions,
                'reads': self.reads, 'writes': self.writes}
