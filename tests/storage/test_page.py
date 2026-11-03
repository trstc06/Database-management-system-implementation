"""Record encoding (Section 2) and the slotted page (Section 3)."""

import unittest
from dbms.common.errors import DbError
from dbms.common.schema import INT, VARCHAR, Column
from dbms.storage.page import MAX_RECORD, PAGE_SIZE, SlottedPage
from dbms.storage.record import decode_record, encode_record

SCHEMA = (Column('R', 'a', INT), Column('R', 'b', VARCHAR, 5), Column('R', 'c', INT))


def empty_page(page_id=0):
    return SlottedPage.format(bytearray(PAGE_SIZE), page_id)


class RecordTests(unittest.TestCase):
    def test_section_2_example_bytes(self):
        record = encode_record((42, 'bob', 7), SCHEMA)
        self.assertEqual(record.hex(' '), '2a 00 00 00 03 00 62 6f 62 07 00 00 00')
        self.assertEqual(len(record), 13)
        self.assertEqual(decode_record(record, SCHEMA), (42, 'bob', 7))

    def test_varchar_length_is_utf8_bytes(self):
        record = encode_record((1, 'été', 2), SCHEMA)  # 5 bytes in UTF-8
        self.assertEqual(decode_record(record, SCHEMA), (1, 'été', 2))
        with self.assertRaises(DbError):
            encode_record((1, 'ééé', 2), SCHEMA)  # 6 bytes > VARCHAR(5)

    def test_int_range_and_types(self):
        self.assertEqual(decode_record(encode_record((-2 ** 31, '', 2 ** 31 - 1), SCHEMA), SCHEMA),
                         (-2 ** 31, '', 2 ** 31 - 1))
        for values in [(2 ** 31, 'a', 0), ('1', 'a', 0), (1, 2, 0), (1, 'a')]:
            with self.subTest(values=values), self.assertRaises(DbError):
                encode_record(values, SCHEMA)


class SlottedPageTests(unittest.TestCase):
    def state(self, page):
        directory = [page.slot(i) for i in range(page.slot_count)]
        return page.slot_count, page.free_end, page.free_space(), directory

    def test_section_3_2_worked_example(self):
        page = empty_page()
        self.assertEqual(self.state(page), (0, 4096, 4088, []))
        self.assertEqual(page.insert(bytes(13)), 0)
        self.assertEqual(self.state(page), (1, 4083, 4071, [(4083, 13)]))
        self.assertEqual(page.insert(bytes(20)), 1)
        self.assertEqual(self.state(page)[:3], (2, 4063, 4047))
        self.assertEqual(page.slot(1), (4063, 20))
        self.assertEqual(page.insert(bytes(13)), 2)
        self.assertEqual(self.state(page)[:3], (3, 4050, 4030))
        self.assertEqual(page.slot(2), (4050, 13))
        page.delete(1)
        self.assertEqual(self.state(page), (3, 4050, 4030, [(4083, 13), (0, 0), (4050, 13)]))

    def test_capacity_for_13_byte_records_is_240(self):
        page = empty_page()
        count = 0
        while page.insert(bytes(13)) is not None:
            count += 1
        self.assertEqual(count, 4088 // (13 + 4))
        self.assertEqual(count, 240)

    def test_largest_record_fits_an_empty_page_exactly(self):
        page = empty_page()
        self.assertEqual(page.insert(bytes(MAX_RECORD)), 0)
        self.assertEqual(page.free_space(), 0)
        self.assertIsNone(empty_page().insert(bytes(MAX_RECORD + 1)))

    def test_get_and_dead_slots(self):
        page = empty_page(7)
        page.insert(b'abc')
        self.assertEqual(page.get(0), b'abc')
        page.delete(0)
        with self.assertRaises(DbError) as result:
            page.get(0)
        self.assertIn('rid (7, 0)', str(result.exception))
        with self.assertRaises(DbError):
            page.delete(0)
        with self.assertRaises(DbError):
            page.get(1)
        self.assertEqual(list(page.live_records()), [])

    def test_slot_numbers_never_reused(self):
        page = empty_page()
        page.insert(b'a')
        page.delete(0)
        self.assertEqual(page.insert(b'b'), 1)
        self.assertEqual(list(page.live_records()), [(1, b'b')])


if __name__ == '__main__':
    unittest.main()
