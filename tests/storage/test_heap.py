"""Heap file operations and the scan rule (Section 4)."""

import tempfile
import unittest
from pathlib import Path
from dbms.common.errors import DbError
from dbms.storage.buffer import BufferPool
from dbms.storage.disk import DiskManager
from dbms.storage.heap import DIRECTORY_CAPACITY, DirectoryPage, HeapFile
from dbms.storage.page import MAX_RECORD


class HeapTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'R.db'
        self.disk = DiskManager(self.path, create=True)

    def tearDown(self):
        self.disk.close()
        self.folder.cleanup()

    def heap(self, frames=8):
        self.pool = BufferPool(frames)
        return HeapFile.create(self.pool, self.disk)

    def test_insert_get_and_rids(self):
        heap = self.heap()
        rids = [heap.insert(bytes([i]) * 13) for i in range(241)]
        self.assertEqual(rids[0], (1, 0))      # page 0 is the directory
        self.assertEqual(rids[239], (1, 239))  # 240 records of 13 bytes per page
        self.assertEqual(rids[240], (2, 0))
        self.assertEqual(heap.get((2, 0)), bytes([240]) * 13)
        self.assertEqual(heap.data_page_count(), 2)

    def test_first_fit_returns_to_an_earlier_page(self):
        heap = self.heap()
        heap.insert(bytes(4000))           # page 1, 84 bytes left
        heap.insert(bytes(100))            # does not fit page 1: page 2
        self.assertEqual(heap.insert(bytes(80)), (1, 1))

    def test_scan_yields_live_records_in_heap_order(self):
        heap = self.heap()
        rids = [heap.insert(str(i).encode()) for i in range(5)]
        heap.delete(rids[1])
        heap.delete(rids[3])
        self.assertEqual([record for _, record in heap.scan()], [b'0', b'2', b'4'])

    def test_deleted_rid_errors_name_the_rid(self):
        heap = self.heap()
        rid = heap.insert(b'x')
        heap.delete(rid)
        for operation in (heap.get, heap.delete):
            with self.subTest(operation=operation.__name__), self.assertRaises(DbError) as result:
                operation(rid)
            self.assertIn('rid (1, 0)', str(result.exception))
        self.assertEqual(self.pool.pinned(), 0)

    def test_delete_everything_then_scan_is_empty(self):
        heap = self.heap()
        rids = [heap.insert(bytes(30)) for _ in range(500)]
        for rid in rids:
            heap.delete(rid)
        self.assertEqual(list(heap.scan()), [])
        self.assertEqual(self.pool.pinned(), 0)

    def test_scan_twenty_times_larger_than_the_pool_pins_one_page(self):
        heap = self.heap(frames=2)
        for i in range(40 * 2):                  # 40 data pages, two records each
            heap.insert(bytes([i % 256]) * 2000)
        self.assertEqual(heap.data_page_count(), 40)
        self.pool.peak_pinned = 0
        before = self.pool.reads
        self.assertEqual(sum(1 for _ in heap.scan()), 80)
        self.assertEqual(self.pool.peak_pinned, 1)
        self.assertGreaterEqual(self.pool.reads - before, 40)
        self.assertEqual(self.pool.pinned(), 0)

    def test_inserts_never_pin_more_than_two_pages(self):
        heap = self.heap(frames=2)
        for _ in range(30):
            heap.insert(bytes(1500))
        self.assertLessEqual(self.pool.peak_pinned, 2)

    def test_stopping_a_scan_early_unpins(self):
        heap = self.heap()
        for _ in range(10):
            heap.insert(b'abc')
        scan = heap.scan()
        next(scan)
        self.assertEqual(self.pool.pinned(), 1)
        scan.close()
        self.assertEqual(self.pool.pinned(), 0)

    def test_record_longer_than_a_page_is_rejected(self):
        heap = self.heap()
        heap.insert(bytes(MAX_RECORD))
        with self.assertRaises(DbError):
            heap.insert(bytes(MAX_RECORD + 1))

    def test_second_directory_page_after_681_entries(self):
        heap = self.heap(frames=4)
        for _ in range(DIRECTORY_CAPACITY + 1):
            heap.insert(bytes(MAX_RECORD))      # one record per data page
        self.assertEqual(heap.data_page_count(), DIRECTORY_CAPACITY + 1)
        self.assertEqual(len(heap.directory_ids()), 2)
        self.assertEqual(sum(1 for _ in heap.scan()), DIRECTORY_CAPACITY + 1)
        frame = self.pool.fetch_page(self.disk, 0)
        self.assertEqual(DirectoryPage(frame.data).entry_count, DIRECTORY_CAPACITY)
        self.pool.unpin_page(self.disk, 0, False)

    def test_contents_survive_reopening(self):
        heap = self.heap()
        rid = heap.insert(b'persist')
        self.pool.flush_all()
        self.disk.close()
        self.disk = DiskManager(self.path)
        reopened = HeapFile(BufferPool(4), self.disk)
        self.assertEqual(reopened.get(rid), b'persist')


if __name__ == '__main__':
    unittest.main()
