"""Module 1 measurements: page capacity, replacement policies, scan cost.

    python3 -m experiments.m1_measure --out experiments/results/m1.json [--traces data/traces]

Every number comes from running the storage engine; nothing is computed
from a formula except the "computed" capacity column it is compared with.
Traces are read from --traces (one page id per line, files sorted by name).
Without published traces, the script builds the one the handout describes
exactly, a sequential scan of an 80 page file repeated ten times.
"""

import argparse
import json
import tempfile
from pathlib import Path
from dbms.common.schema import INT, VARCHAR, Column
from dbms.storage.buffer import BufferPool
from dbms.storage.database import Database
from dbms.storage.disk import NullDisk
from dbms.storage.page import USABLE_SPACE

POOL_SIZES = (4, 16, 64, 256)
SCAN_FRAMES = (2, 8, 64)
SCAN_PAGES = 500


def capacity_schemas() -> list[dict]:
    """Fixed-length values, so the record length is known exactly by hand."""
    return [
        {'name': 'all INT', 'columns': [('a', INT, None), ('b', INT, None), ('c', INT, None)],
         'row': lambda i: (i, i, i), 'record_bytes': 4 + 4 + 4},
        {'name': 'short VARCHAR', 'columns': [('a', INT, None), ('b', VARCHAR, 10), ('c', INT, None)],
         'row': lambda i: (i, f'{i:010d}', i), 'record_bytes': 4 + (2 + 10) + 4},
        {'name': 'VARCHAR(200)', 'columns': [('a', INT, None), ('b', VARCHAR, 200)],
         'row': lambda i: (i, f'{i:0200d}'), 'record_bytes': 4 + (2 + 200)},
    ]


def build_table(directory: Path, name: str, columns: list, rows) -> Database:
    db = Database(directory, frames=8, create=True)
    db.create_table(name, tuple(Column(name, c, t, n) for c, t, n in columns))
    table = db.table(name)
    count = 0
    for row in rows:
        table.insert(row)
        count += 1
    db.save_counts(table, count)
    return db


def measure_capacity(work: Path) -> list[dict]:
    results = []
    for index, schema in enumerate(capacity_schemas()):
        computed = USABLE_SPACE // (schema['record_bytes'] + 4)
        rows = (schema['row'](i) for i in range(computed * 3))   # three full pages
        with build_table(work / f'capacity{index}', 'C', schema['columns'], rows) as db:
            table = db.table('C')
            per_page = {}
            for (page_id, _), _ in table.scan():
                per_page[page_id] = per_page.get(page_id, 0) + 1
            measured = max(per_page.values())
        results.append({'schema': schema['name'], 'record_bytes': schema['record_bytes'],
                        'computed': computed, 'measured': measured, 'pages': len(per_page)})
    return results


def sequential_trace(pages: int = 80, repeats: int = 10) -> list[int]:
    return list(range(pages)) * repeats


def read_traces(folder: Path | None) -> dict[str, list[int]]:
    if folder is None:
        return {'trace3 (rebuilt from the handout)': sequential_trace()}
    traces = {}
    for path in sorted(folder.iterdir()):
        if path.is_file():
            traces[path.name] = [int(line) for line in path.read_text().split()]
    return traces


def replay(pages: list[int], frames: int, policy: str) -> dict:
    pool, disk = BufferPool(frames, policy), NullDisk()
    for page_id in pages:
        pool.fetch_page(disk, page_id)
        pool.unpin_page(disk, page_id, False)
    stats = pool.stats()
    return {**stats, 'hit_rate': stats['hits'] / len(pages)}


def measure_policies(traces: dict[str, list[int]]) -> list[dict]:
    return [{'trace': name, 'policy': policy, 'frames': frames, 'fetches': len(pages),
             **replay(pages, frames, policy)}
            for policy in ('lru', 'clock') for name, pages in traces.items() for frames in POOL_SIZES]


def measure_scan_cost(work: Path) -> list[dict]:
    record_bytes = 4 + 2 + 200
    per_page = USABLE_SPACE // (record_bytes + 4)
    rows = ((i, f'{i:0200d}') for i in range(per_page * SCAN_PAGES))
    build_table(work / 'scan', 'S', [('a', INT, None), ('b', VARCHAR, 200)], rows).close()
    results = []
    for frames in SCAN_FRAMES:
        with Database(work / 'scan', frames=frames) as db:
            table = db.table('S')
            count = sum(1 for _ in table.rows())
            results.append({'frames': frames, 'data_pages': table.info.num_pages, 'rows': count,
                            **db.pool.stats(), 'peak_pinned': db.pool.peak_pinned})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--traces', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        results = {'capacity': measure_capacity(work),
                   'policies': measure_policies(read_traces(args.traces)),
                   'scan_cost': measure_scan_cost(work)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
