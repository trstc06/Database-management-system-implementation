"""One shell session: SQL text in, rows out, through all four modules.

    M3 compile_sql -> M4 Planner -> M2 run_plan -> M1 buffer pool and heap files

Besides the SQL subset the shell takes EXPLAIN, EXPLAIN ANALYZE,
ANALYZE table, SET frames = n and LOAD table FROM file. Each statement
opens the database with a cold buffer pool of the current frame count,
so the page I/O it reports does not depend on what ran before it.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from ..common.errors import DbError
from ..common.schema import INT, VARCHAR, Column
from ..execution.cli import format_rows
from ..execution.run import run_plan
from ..optimizer.cli import sql_catalog, table_columns
from ..optimizer.explain import explain_physical, node_label
from ..optimizer.planner import MIN_FRAMES, Planner
from ..optimizer.stats import DEFAULT_BUCKETS, analyze, load_stats
from ..sql.translate import compile_sql
from ..storage.database import CATALOG_FILE, Database
from ..storage.loader import load_csv
from .statements import blank_out, first_word

DEFAULT_FRAMES = 32


@dataclass(frozen=True)
class QueryRun:
    """What the workload report needs from one query."""
    rows: int
    page_io: int
    seconds: float
    est_root_rows: int
    output: str


def walk(plan: dict) -> list[dict]:
    nodes = [plan]
    for key in ('child', 'left', 'right'):
        if key in plan:
            nodes += walk(plan[key])
    return nodes


class Session:
    def __init__(self, directory: Path, frames: int = DEFAULT_FRAMES) -> None:
        self.directory = Path(directory)
        self.frames = frames
        if not (self.directory / CATALOG_FILE).exists():
            Database(self.directory, frames=frames, create=True).close()

    def database(self) -> Database:
        return Database(self.directory, frames=self.frames)

    def execute(self, text: str) -> str:
        """Run one statement (without its semicolon) and return what to print."""
        word, end = first_word(text)
        if word == 'EXPLAIN':
            rest = blank_out(text, end)
            second, after = first_word(rest)
            if second == 'ANALYZE':
                return self.explain_analyze(blank_out(rest, after))
            return self.explain(rest)
        if word == 'ANALYZE':
            return self.analyze(text[end:])
        if word == 'SET':
            return self.set_option(text[end:])
        if word == 'LOAD':
            return self.load(text[end:])
        return self.sql(text)

    # SQL ----------------------------------------------------------------

    def compile_one(self, db, text: str) -> dict:
        plans = compile_sql(text, sql_catalog(db))
        if len(plans) != 1:
            raise DbError('syntax', f'expected one statement, found {len(plans)}')
        return plans[0]

    def physical(self, db, logical: dict) -> dict:
        if logical['op'] in ('create_table', 'insert'):
            raise DbError('syntax', 'EXPLAIN works on queries, not CREATE TABLE or INSERT')
        return Planner(table_columns(db), load_stats(db), self.frames).plan(logical)

    def sql(self, text: str) -> str:
        with self.database() as db:
            logical = self.compile_one(db, text)
            if logical['op'] == 'create_table':
                return self.create_table(db, logical)
            if logical['op'] == 'insert':
                return self.insert(db, logical)
            return self.run_query(db, logical).output

    def run_query(self, db, logical: dict) -> QueryRun:
        started = time.perf_counter()
        physical = self.physical(db, logical)
        result = run_plan(db, physical, self.frames)
        seconds = time.perf_counter() - started
        totals = result.totals()
        return QueryRun(len(result.rows), totals['page_reads'] + totals['page_writes'], seconds,
                        physical['est_rows'], format_rows(result))

    def query(self, text: str) -> QueryRun:
        with self.database() as db:
            logical = self.compile_one(db, text)
            if logical['op'] in ('create_table', 'insert'):
                raise DbError('syntax', 'a workload holds queries only')
            return self.run_query(db, logical)

    def create_table(self, db, plan: dict) -> str:
        columns = tuple(Column(plan['table'], spec[0], spec[1], spec[2] if spec[1] == VARCHAR else None)
                        for spec in plan['columns'])
        db.create_table(plan['table'], columns)
        return f"CREATE TABLE {plan['table']}"

    def insert(self, db, plan: dict) -> str:
        table = db.table(plan['table'])
        table.insert(tuple(plan['values']))
        db.save_counts(table, table.info.num_tuples + 1)
        return 'INSERT 1'

    # EXPLAIN ------------------------------------------------------------

    def explain(self, text: str) -> str:
        with self.database() as db:
            return '\n'.join(explain_physical(self.physical(db, self.compile_one(db, text))))

    def explain_analyze(self, text: str) -> str:
        """The plan with estimated and actual rows at every node, then the totals."""
        with self.database() as db:
            started = time.perf_counter()
            physical = self.physical(db, self.compile_one(db, text))
            result = run_plan(db, physical, self.frames)
            seconds = time.perf_counter() - started
        lines = []
        for node, (depth, _, counts) in zip(walk(physical), result.nodes):
            loops = max(counts['opens'], 1)
            actual = f"rows={round(counts['tuples_out'] / loops)}" + (f' loops={loops}' if loops > 1 else '')
            label = '  ' * depth + node_label(node)
            lines.append(f"{label:<52} est_rows={node['est_rows']:<8} {actual:<20} "
                         f"est_cost={node['est_cost']:<7} reads={counts['page_reads']} writes={counts['page_writes']}")
        totals = result.totals()
        lines.append(f"rows: {len(result.rows)}   page I/O: {totals['page_reads'] + totals['page_writes']} "
                     f"(est {physical['est_cost']})   time: {seconds * 1000:.1f} ms   frames: {self.frames}")
        return '\n'.join(lines)

    # Shell commands -----------------------------------------------------

    def analyze(self, rest: str) -> str:
        name = rest.strip()
        if not name or not all(c.isalnum() or c == '_' for c in name):
            raise DbError('syntax', 'write ANALYZE followed by one table name')
        with self.database() as db:
            records = analyze(db, name, DEFAULT_BUCKETS)
        columns = sum(1 for r in records if r[0] == 3)
        return f'ANALYZE {name}: {columns} columns, {len(records) - columns} histogram buckets'

    def set_option(self, rest: str) -> str:
        name, equals, value = rest.partition('=')
        if name.strip().lower() != 'frames' or not equals:
            raise DbError('syntax', 'the only setting is SET frames = n')
        value = value.strip()
        if not value.isdigit() or int(value) < MIN_FRAMES:
            raise DbError('syntax', f'frames must be a whole number of at least {MIN_FRAMES}, got {value!r}')
        self.frames = int(value)
        return f'SET frames = {self.frames}'

    def load(self, rest: str) -> str:
        name, separator, file = split_load(rest)
        if not name or separator.upper() != 'FROM' or not file:
            raise DbError('syntax', 'write LOAD table FROM file')
        with self.database() as db:
            loaded = load_csv(db, name, Path(file))
        return f'LOAD {loaded} rows into {name}'


def split_load(rest: str) -> tuple[str, str, str]:
    """'Emp FROM data/emp.csv' or "Emp FROM 'my file.csv'" -> (Emp, FROM, path)."""
    parts = rest.strip().split(None, 2)
    if len(parts) < 3:
        return '', '', ''
    name, separator, file = parts
    file = file.strip()
    if len(file) >= 2 and file[0] == file[-1] and file[0] in "'\"":
        file = file[1:-1]
    return name, separator, file
