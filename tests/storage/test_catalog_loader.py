"""Catalog persistence (Section 6) and the CSV loader."""

import tempfile
import unittest
from pathlib import Path
from dbms.common.errors import DbError
from dbms.common.schema import INT, VARCHAR, Column
from dbms.storage.database import Database
from dbms.storage.loader import load_csv, split_fields

SCHEMA = (Column('R', 'a', INT), Column('R', 'b', VARCHAR, 20), Column('R', 'c', INT))


class StorageCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.db_dir = self.root / 'db'

    def tearDown(self):
        self.folder.cleanup()

    def write_csv(self, text, name='r.csv'):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return path

    def create(self, schema=SCHEMA, name='R'):
        with Database(self.db_dir, create=True) as db:
            db.create_table(name, schema)


class CatalogTests(StorageCase):
    def test_tables_and_counts_survive_reopening(self):
        self.create()
        self.create((Column('S', 'x', INT),), 'S')
        with Database(self.db_dir) as db:
            load_csv(db, 'R', self.write_csv('1,bob,2\n3,amy,4\n'))
        with Database(self.db_dir) as db:
            self.assertEqual(db.catalog.list_tables(), ['R', 'S'])
            info = db.catalog.get_table('R')
            self.assertEqual(info.columns, SCHEMA)
            self.assertEqual((info.file, info.num_pages, info.num_tuples, info.first_page), ('R.db', 1, 2, 0))
            self.assertEqual(list(db.table('R').rows()), [(1, 'bob', 2), (3, 'amy', 4)])

    def test_bad_schemas_are_rejected(self):
        for name, schema in [('R', ()), ('1R', SCHEMA), ('../x', SCHEMA), ('catalog', SCHEMA),
                             ('R', (Column('R', 'a', INT), Column('R', 'a', INT))),
                             ('R', (Column('R', 'b', VARCHAR, 256),)),
                             ('R', (Column('R', 'b', VARCHAR, None),))]:
            with self.subTest(name=name, schema=schema), self.assertRaises(DbError):
                with Database(self.db_dir, create=True) as db:
                    db.create_table(name, schema)

    def test_duplicate_and_unknown_tables(self):
        self.create()
        with self.assertRaises(DbError):
            self.create()
        with Database(self.db_dir) as db, self.assertRaises(DbError) as result:
            db.table('Missing')
        self.assertIn('unknown table Missing', str(result.exception))

    def test_opening_a_folder_that_is_not_a_database(self):
        with self.assertRaises(DbError):
            Database(self.root / 'nothing')


class LoaderTests(StorageCase):
    def test_split_fields_quotes_and_commas(self):
        self.assertEqual(split_fields('1,"a,b",2', 1), ['1', 'a,b', '2'])
        self.assertEqual(split_fields('"say ""hi""",,x', 1), ['say "hi"', '', 'x'])
        for line in ['"open,1', '"a"b,1']:
            with self.subTest(line=line), self.assertRaises(DbError):
                split_fields(line, 4)

    def test_errors_name_the_line_and_column(self):
        cases = [('1,a,2\nx,b,3\n', 'line 2: column 1 (a)'),
                 ('1,a,2\n2,' + 'z' * 21 + ',3\n', 'line 2: column 2 (b)'),
                 ('1,a\n', 'line 1: expected 3 fields, got 2'),
                 ('1,a,99999999999\n', 'line 1: column 3 (c)')]
        for text, message in cases:
            with self.subTest(text=text):
                self.folder.cleanup()
                self.folder = tempfile.TemporaryDirectory()
                self.root = Path(self.folder.name)
                self.db_dir = self.root / 'db'
                self.create()
                with Database(self.db_dir) as db:
                    with self.assertRaises(DbError) as result:
                        load_csv(db, 'R', self.write_csv(text))
                    self.assertIn(message, str(result.exception))
                    self.assertEqual(list(db.table('R').rows()), [])  # load stopped before inserting

    def test_record_too_long_names_table_and_row(self):
        wide = tuple(Column('W', f'v{i}', VARCHAR, 255) for i in range(17))
        self.create(wide, 'W')
        with Database(self.db_dir) as db, self.assertRaises(DbError) as result:
            load_csv(db, 'W', self.write_csv(','.join(['x' * 255] * 17) + '\n'))
        self.assertIn('table W, line 1', str(result.exception))

    def test_blank_lines_crlf_and_signs(self):
        self.create()
        with Database(self.db_dir) as db:
            self.assertEqual(load_csv(db, 'R', self.write_csv('-1,"x,y",+2\r\n\r\n3,,4\r\n')), 2)
            self.assertEqual(list(db.table('R').rows()), [(-1, 'x,y', 2), (3, '', 4)])


if __name__ == '__main__':
    unittest.main()
