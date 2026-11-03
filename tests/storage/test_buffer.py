"""Buffer pool counting rules and both policies (Section 5)."""

import tempfile
import unittest
from pathlib import Path
from dbms.common.errors import DbError
from dbms.storage.buffer import BufferPool
from dbms.storage.disk import DiskManager, NullDisk


def run_trace(policy, frames, pages):
    pool = BufferPool(frames, policy)
    disk = NullDisk()
    resident = []
    for page_id in pages:
        pool.fetch_page(disk, page_id)
        pool.unpin_page(disk, page_id, False)
        resident = [frame.page_id for frame in pool.frames if frame.key is not None]
    return pool, resident


class WorkedExampleTests(unittest.TestCase):
    TRACE = [1, 2, 3, 1, 4, 2]

    def test_section_5_3_lru(self):
        pool, _ = run_trace('lru', 3, self.TRACE)
        self.assertEqual(pool.stats(), {'hits': 1, 'misses': 5, 'evictions': 2, 'reads': 5, 'writes': 0})
        self.assertEqual(sorted(frame.page_id for frame in pool.frames), [1, 2, 4])

    def test_section_5_3_clock(self):
        pool, resident = run_trace('clock', 3, self.TRACE)
        self.assertEqual(pool.stats(), {'hits': 2, 'misses': 4, 'evictions': 1, 'reads': 4, 'writes': 0})
        self.assertEqual(resident, [4, 2, 3])  # the table's pool after fetch 4

    def test_clock_step_by_step(self):
        pool = BufferPool(3, 'clock')
        disk = NullDisk()
        for page_id in [1, 2, 3, 1]:
            pool.fetch_page(disk, page_id)
            pool.unpin_page(disk, page_id, False)
        self.assertEqual((pool.policy.ref, pool.policy.hand), ([1, 1, 1], 0))
        pool.fetch_page(disk, 4)  # sweep clears 1, 2, 3 then evicts 1
        self.assertEqual((pool.policy.ref, pool.policy.hand), ([1, 0, 0], 1))

    def test_first_b_misses_are_not_evictions(self):
        for policy in ('lru', 'clock'):
            with self.subTest(policy=policy):
                pool, _ = run_trace(policy, 4, [0, 1, 2, 3])
                self.assertEqual((pool.misses, pool.evictions), (4, 0))

    def test_sequential_flooding_under_lru(self):
        # Five pages cycled through four frames: LRU always evicts the next page needed.
        pool, _ = run_trace('lru', 4, list(range(5)) * 3)
        self.assertEqual(pool.hits, 0)


class PinningTests(unittest.TestCase):
    def test_evicting_a_pinned_page_fails(self):
        for policy in ('lru', 'clock'):
            with self.subTest(policy=policy):
                pool, disk = BufferPool(2, policy), NullDisk()
                pool.fetch_page(disk, 1)
                pool.fetch_page(disk, 2)
                with self.assertRaises(DbError) as result:
                    pool.fetch_page(disk, 3)
                self.assertIn('no unpinned frame available', str(result.exception))
                self.assertEqual(sorted(f.page_id for f in pool.frames), [1, 2])

    def test_unpin_at_pin_count_zero_is_an_error(self):
        pool, disk = BufferPool(2), NullDisk()
        pool.fetch_page(disk, 1)
        pool.unpin_page(disk, 1, False)
        with self.assertRaises(DbError) as result:
            pool.unpin_page(disk, 1, False)
        self.assertIn('pin count is already 0', str(result.exception))
        with self.assertRaises(DbError):
            pool.unpin_page(disk, 9, False)

    def test_hit_pins_again_and_counts(self):
        pool, disk = BufferPool(2), NullDisk()
        first = pool.fetch_page(disk, 1)
        second = pool.fetch_page(disk, 1)
        self.assertIs(first, second)
        self.assertEqual((first.pin_count, pool.hits, pool.misses), (2, 1, 1))
        self.assertEqual(pool.peak_pinned, 1)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.disk = DiskManager(Path(self.folder.name) / 'T.db', create=True)

    def tearDown(self):
        self.disk.close()
        self.folder.cleanup()

    def test_dirty_victim_written_once_and_read_back(self):
        pool = BufferPool(1)
        frame = pool.new_page(self.disk)
        frame.data[0:3] = b'abc'
        pool.unpin_page(self.disk, 0, True)
        pool.new_page(self.disk)  # evicts page 0, which is dirty
        pool.unpin_page(self.disk, 1, True)
        self.assertEqual(pool.writes, 1)
        frame = pool.fetch_page(self.disk, 0)
        self.assertEqual(bytes(frame.data[:3]), b'abc')
        pool.unpin_page(self.disk, 0, False)
        pool.flush_all()
        self.assertEqual(pool.stats(), {'hits': 0, 'misses': 1, 'evictions': 1, 'reads': 1, 'writes': 2})
        self.assertEqual((self.disk.reads, self.disk.writes), (1, 2))

    def test_flush_writes_each_dirty_frame_once(self):
        pool = BufferPool(3)
        for _ in range(3):
            frame = pool.new_page(self.disk)
            pool.unpin_page(self.disk, frame.page_id, False)
        pool.flush_all()
        pool.flush_all()
        self.assertEqual(pool.writes, 3)
        self.assertEqual(self.disk.num_pages(), 3)

    def test_reading_outside_the_file_fails(self):
        with self.assertRaises(DbError):
            BufferPool(2).fetch_page(self.disk, 0)


if __name__ == '__main__':
    unittest.main()
