"""The integrated shell: every command in Section 6, scripts, the workload report."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from dbms.common.errors import DbError
from dbms.shell.session import Session
from dbms.shell.statements import split_statements
from dbms.shell.workload import format_report, run_workload

ROOT = Path(__file__).resolve().parents[2]
EMP_CSV = '1,ann,34,1\n2,bob,61,2\n3,"o\'neil, c",45,1\n4,dee,61,2\n'


def tool(name: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / 'bin' / name), *args], input=stdin, capture_output=True, text=True)


class StatementSplitting(unittest.TestCase):
    def test_semicolons_in_strings_and_comments_do_not_split(self):
        statements, rest = split_statements("INSERT INTO T VALUES ('a;b'); -- note; here\nSELECT a FROM T; SEL")
        self.assertEqual([s.strip() for s in statements], ["INSERT INTO T VALUES ('a;b')",
                                                           '-- note; here\nSELECT a FROM T'])
        self.assertEqual(rest.strip(), 'SEL')

    def test_statements_keep_their_line_and_column(self):
        statements, _ = split_statements('SELECT a FROM T;\n  SELECT b FROM T;')
        second = statements[1]
        self.assertEqual(second.splitlines()[1].index('SELECT'), 2)


class SessionCommands(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        (self.work / 'emp.csv').write_text(EMP_CSV, encoding='utf-8')
        self.session = Session(self.work / 'db', frames=8)
        for text in ('CREATE TABLE Emp (eid INT, name VARCHAR(20), age INT, did INT)',
                     'CREATE TABLE Dept (did INT, title VARCHAR(20))',
                     f"LOAD Emp FROM '{self.work / 'emp.csv'}'",
                     "INSERT INTO Dept VALUES (1, 'sales')", "INSERT INTO Dept VALUES (2, 'it')",
                     'ANALYZE Emp', 'ANALYZE Dept'):
            self.session.execute(text)

    def test_query_returns_rows(self):
        output = self.session.execute('SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did '
                                      'WHERE e.age > 40 ORDER BY e.name')
        self.assertEqual(output.splitlines(), ['e.name | d.title', 'bob | it', 'dee | it',
                                               "o'neil, c | sales", '(3 rows)'])

    def test_count_star_on_an_empty_table_is_one_row(self):
        self.session.execute('CREATE TABLE Empty (a INT)')
        self.assertEqual(self.session.execute('SELECT COUNT(*) FROM Empty').splitlines()[1:], ['0', '(1 rows)'])

    def test_explain_shows_estimates_without_running(self):
        output = self.session.execute('EXPLAIN SELECT name FROM Emp WHERE age > 40')
        self.assertIn('seqscan Emp', output)
        self.assertIn('est_rows=', output)
        self.assertNotIn('rows: ', output)

    def test_explain_analyze_shows_estimated_and_actual(self):
        output = self.session.execute('explain analyze SELECT name FROM Emp WHERE age > 40')
        self.assertIn('filter (Emp.age > 40)', output)
        self.assertIn('rows=3', output)
        self.assertTrue(output.splitlines()[-1].startswith('rows: 3   page I/O: '))

    def test_set_frames_changes_the_budget(self):
        self.assertEqual(self.session.execute('SET frames = 4'), 'SET frames = 4')
        self.assertIn('frames: 4', self.session.execute('EXPLAIN ANALYZE SELECT name FROM Emp'))
        with self.assertRaises(DbError):
            self.session.execute('SET frames = 2')
        with self.assertRaises(DbError):
            self.session.execute('SET buffers = 8')

    def test_analyze_and_load_report_what_they_did(self):
        self.assertEqual(self.session.execute('ANALYZE Dept'), 'ANALYZE Dept: 2 columns, 4 histogram buckets')
        self.assertEqual(self.session.execute(f"LOAD Emp FROM {self.work / 'emp.csv'}"), 'LOAD 4 rows into Emp')
        self.assertIn('(8 rows)', self.session.execute('SELECT eid FROM Emp'))

    def test_errors_use_the_one_format(self):
        with self.assertRaises(DbError) as caught:
            self.session.execute('SELECT nope FROM Emp')
        self.assertEqual(caught.exception.unified(), "error: name: unknown column 'nope' at line 1, column 8")
        with self.assertRaises(DbError) as caught:
            self.session.execute('EXPLAIN SELECT nope FROM Emp')
        self.assertEqual(caught.exception.column, 16)   # still the column in what was typed

    def test_workload_report_has_every_required_column(self):
        queries = self.work / 'q.sql'
        queries.write_text('SELECT name FROM Emp WHERE age > 40;\nSELECT COUNT(*) FROM Emp;\nSELECT x FROM Emp;\n')
        rows = run_workload(self.session, queries)
        self.assertEqual([r.rows for r in rows], [3, 1, None])
        self.assertTrue(all(r.page_io > 0 and r.milliseconds >= 0 for r in rows[:2]))
        report = format_report(rows, self.session.frames)
        self.assertIn('| Query | Rows | Page I/O | Wall time (ms) | Est. rows at root | Actual rows at root |', report)
        self.assertIn('unknown column', report)


class CommandLine(unittest.TestCase):
    def test_script_file_and_piped_prompt(self):
        work = Path(tempfile.mkdtemp())
        script = work / 'setup.sql'
        script.write_text("CREATE TABLE T (a INT);\nINSERT INTO T VALUES (7);\nSELECT a FROM T;\n")
        result = tool('dbms', '--db', str(work / 'db'), '--file', str(script))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-2:], ['7', '(1 rows)'])
        piped = tool('dbms', '--db', str(work / 'db'), stdin='SELECT a\nFROM T;\nquit\nSELECT b FROM T;\n')
        self.assertEqual((piped.returncode, piped.stderr), (0, ''))
        failed = tool('dbms', '--db', str(work / 'db'), stdin='SELECT b FROM T;\n')
        self.assertEqual(failed.returncode, 1)
        self.assertTrue(failed.stderr.startswith('error: name:'))

    def test_the_four_module_tools_still_work(self):
        for name in ('ra', 'storage', 'exec', 'sql', 'opt', 'dbms'):
            with self.subTest(tool=name):
                self.assertEqual(tool(name, '--help').returncode, 0)


if __name__ == '__main__':
    unittest.main()
