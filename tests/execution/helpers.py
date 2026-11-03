"""Build small databases on disk for the execution tests."""

import random
import tempfile
from collections import Counter
from pathlib import Path
from dbms.common.schema import INT, VARCHAR, Column
from dbms.execution.run import run_plan
from dbms.storage.database import Database


def make_db(tables: dict) -> Path:
    """tables: name -> (["a:INT", "b:VARCHAR(10)"], rows)."""
    directory = Path(tempfile.mkdtemp()) / 'db'
    with Database(directory, frames=8, create=True) as db:
        for name, (spec, rows) in tables.items():
            columns = []
            for item in spec:
                column, kind = item.split(':')
                if kind == 'INT':
                    columns.append(Column(name, column, INT))
                else:
                    columns.append(Column(name, column, VARCHAR, int(kind[8:-1])))
            db.create_table(name, tuple(columns))
            table = db.table(name)
            for row in rows:
                table.insert(row)
            db.save_counts(table, len(rows))
        db.pool.flush_all()
    return directory


def run(directory: Path, plan: dict, frames: int):
    with Database(directory, frames=frames) as db:
        return run_plan(db, plan, frames)


def scan(table: str) -> dict:
    return {'op': 'seqscan', 'table': table}


def bag(rows) -> Counter:
    return Counter(tuple(row) for row in rows)


def random_rows(count: int, keys: int, seed: int) -> list:
    rng = random.Random(seed)
    return [(i, rng.randrange(keys), rng.randrange(1000)) for i in range(count)]
