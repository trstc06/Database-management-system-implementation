"""Module 2 measurements (Section 5): joins against formulas, sort passes,
crossover, skew and the memory budget.

    python3 -m experiments.m2_measure --out experiments/results/m2.json --plot docs/m2_crossover.svg

Tables use rows of (INT, VARCHAR(200)) so a page holds 19 tuples. That keeps
block nested loop's |R| x |S| comparisons small enough to run in Python
while the page counts stay large. Every number comes from running plans.
"""

import argparse
import json
import math
import random
import tempfile
from pathlib import Path
from dbms.common.schema import INT, VARCHAR, Column
from dbms.execution.base import END
from dbms.execution.context import Context
from dbms.execution.plan import build, plan_nodes
from dbms.storage.database import Database

ROWS_PER_PAGE = 19
JOINS = ('nested_loop_join', 'sort_merge_join', 'hash_join')
COST_FRAMES = (4, 16, 64, 256)
COST_SIZES = (8, 32, 96)
SORT_CASES = ((8, 3), (30, 4), (80, 4), (100, 8), (500, 16), (64, 64))
CROSSOVER_FRAMES = (4, 6, 8, 12, 16, 32, 64)
CROSSOVER_SIZE = 32
CROSSOVER_PAGES = (4, 8, 16, 32, 64, 96)
CROSSOVER_FIXED_FRAMES = 8
SKEW_ROWS = 1500
SKEW_FRAMES = 16
BUDGET_FRAMES = 4


def make_table(db: Database, name: str, keys: list[int]) -> None:
    db.create_table(name, (Column(name, 'k', INT), Column(name, 'pad', VARCHAR, 200)))
    table = db.table(name)
    for i, key in enumerate(keys):
        table.insert((key, f'{i:0200d}'))
    db.save_counts(table, len(keys))


def make_db(directory: Path, tables: dict) -> Path:
    with Database(directory, frames=8, create=True) as db:
        for name, keys in tables.items():
            make_table(db, name, keys)
        db.pool.flush_all()
    return directory


def uniform_keys(pages: int, seed: int) -> list[int]:
    rows = pages * ROWS_PER_PAGE
    rng = random.Random(seed)
    return [rng.randrange(rows) for _ in range(rows)]


def execute(directory: Path, plan: dict, frames: int) -> dict:
    """Run a plan and return totals, the root operator and the peak frames held."""
    with Database(directory, frames=frames) as db:
        ctx = Context(db, frames)
        try:
            root = build(ctx, plan)
            root.open()
            count = 0
            while root.next() is not END:
                count += 1
            root.close()
            problems = ctx.leftovers()
            if problems:
                raise RuntimeError('; '.join(problems))
            nodes = [op.counters() for _, op in plan_nodes(root)]
            return {'root': root, 'rows': count, 'peak': db.pool.peak_pinned,
                    'reads': sum(n['page_reads'] for n in nodes),
                    'writes': sum(n['page_writes'] for n in nodes),
                    'comparisons': sum(n['comparisons'] for n in nodes)}
        finally:
            ctx.remove_temp_dir()


def join_plan(op: str) -> dict:
    return {'op': op, 'condition': 'R.k = S.k',
            'left': {'op': 'seqscan', 'table': 'R'}, 'right': {'op': 'seqscan', 'table': 'S'}}


def sort_passes(pages: int, frames: int) -> int:
    runs = math.ceil(pages / frames)
    return 1 if runs <= 1 else 1 + math.ceil(math.log(runs) / math.log(frames - 1))


def formula(op: str, m: int, n: int, frames: int) -> int:
    """Section 2 page I/O. sort(X) is the textbook 2X per pass."""
    if op == 'nested_loop_join':
        return m + math.ceil(m / (frames - 2)) * n
    if op == 'hash_join':
        return 3 * (m + n)
    return 2 * m * sort_passes(m, frames) + 2 * n * sort_passes(n, frames) + m + n


def measure_costs(work: Path) -> list[dict]:
    rows = []
    for pages in COST_SIZES:
        directory = make_db(work / f'cost{pages}', {'R': uniform_keys(pages, 1), 'S': uniform_keys(pages, 2)})
        for frames in COST_FRAMES:
            for op in JOINS:
                result = execute(directory, join_plan(op), frames)
                rows.append({'join': op, 'M': pages, 'N': pages, 'B': frames,
                             'reads': result['reads'], 'writes': result['writes'],
                             'measured': result['reads'] + result['writes'],
                             'formula': formula(op, pages, pages, frames), 'output': result['rows']})
    return rows


def measure_sort_passes(work: Path) -> list[dict]:
    rows = []
    for pages, frames in SORT_CASES:
        directory = make_db(work / f'sort{pages}_{frames}', {'R': uniform_keys(pages, 3)})
        plan = {'op': 'sort', 'keys': [['R.k', 'asc']], 'child': {'op': 'seqscan', 'table': 'R'}}
        result = execute(directory, plan, frames)
        rows.append({'N': pages, 'B': frames, 'formula': sort_passes(pages, frames),
                     'measured': result['root'].passes, 'peak': result['peak']})
    return rows


def cheapest(directory: Path, pages: int, frames: int) -> dict:
    costs = {op: sum(execute(directory, join_plan(op), frames)[k] for k in ('reads', 'writes')) for op in JOINS}
    return {'pages': pages, 'B': frames, 'costs': costs, 'cheapest': min(costs, key=costs.get)}


def measure_crossover(work: Path) -> dict:
    fixed = make_db(work / 'cross_fixed', {'R': uniform_keys(CROSSOVER_SIZE, 4), 'S': uniform_keys(CROSSOVER_SIZE, 5)})
    by_frames = [cheapest(fixed, CROSSOVER_SIZE, frames) for frames in CROSSOVER_FRAMES]
    by_size = []
    for pages in CROSSOVER_PAGES:
        directory = make_db(work / f'cross{pages}', {'R': uniform_keys(pages, 6), 'S': uniform_keys(pages, 7)})
        by_size.append(cheapest(directory, pages, CROSSOVER_FIXED_FRAMES))
    return {'by_frames': by_frames, 'by_size': by_size}


def skew_keys(seed: int) -> list[int]:
    """40 percent of rows share key 0; the rest are distinct."""
    rng = random.Random(seed)
    keys = [0 if i < SKEW_ROWS * 2 // 5 else i for i in range(SKEW_ROWS)]
    rng.shuffle(keys)
    return keys


def measure_skew(work: Path) -> dict:
    out = {}
    cases = {'uniform': (list(range(SKEW_ROWS)), list(range(SKEW_ROWS))), 'skewed': (skew_keys(8), skew_keys(9))}
    for label, (r_keys, s_keys) in cases.items():
        directory = make_db(work / f'skew_{label}', {'R': r_keys, 'S': s_keys})
        result = execute(directory, join_plan('hash_join'), SKEW_FRAMES)
        join = result['root']
        out[label] = {'B': SKEW_FRAMES, 'partition_pages': join.partition_pages,
                      'overflow': [list(step) for step in join.overflows],
                      'reads': result['reads'], 'writes': result['writes'], 'rows': result['rows']}
    return out


def measure_budget(work: Path) -> dict:
    pages = 20 * BUDGET_FRAMES
    directory = make_db(work / 'budget', {'R': uniform_keys(pages, 10)})
    plan = {'op': 'sort', 'keys': [['R.k', 'asc']], 'child': {'op': 'seqscan', 'table': 'R'}}
    first, second = (execute(directory, plan, BUDGET_FRAMES) for _ in range(2))
    same = all(first[k] == second[k] for k in ('reads', 'writes', 'comparisons', 'rows'))
    return {'pages': pages, 'B': BUDGET_FRAMES, 'rows': first['rows'], 'peak_frames': first['peak'],
            'passes': first['root'].passes, 'reads': first['reads'], 'writes': first['writes'],
            'same_counters_twice': same}


def plot(crossover: dict, path: Path) -> None:
    """Two small line charts as one SVG: cost against B, and cost against size."""
    colors = {'nested_loop_join': '#c0392b', 'sort_merge_join': '#2471a3', 'hash_join': '#1e8449'}
    panels = [('Cost vs B (M = N = 32 pages)', crossover['by_frames'], 'B'),
              (f'Cost vs size (B = {CROSSOVER_FIXED_FRAMES})', crossover['by_size'], 'pages')]
    width, height, pad = 420, 300, 50
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{2 * width}" height="{height + 30}" '
             'font-family="sans-serif" font-size="11"><rect width="100%" height="100%" fill="white"/>']
    for index, (title, points, axis) in enumerate(panels):
        left = index * width
        xs = [math.log2(p[axis]) for p in points]
        top = max(max(p['costs'].values()) for p in points)
        scale_y = lambda v: height - pad - (height - 2 * pad) * math.log10(v) / math.log10(top)
        scale_x = lambda v: left + pad + (width - 2 * pad) * (v - min(xs)) / (max(xs) - min(xs))
        parts.append(f'<text x="{left + width / 2}" y="20" text-anchor="middle" font-size="13">{title}</text>')
        parts.append(f'<line x1="{left + pad}" y1="{height - pad}" x2="{left + width - pad}" y2="{height - pad}" stroke="black"/>')
        parts.append(f'<line x1="{left + pad}" y1="{pad}" x2="{left + pad}" y2="{height - pad}" stroke="black"/>')
        for point, x in zip(points, xs):
            parts.append(f'<text x="{scale_x(x)}" y="{height - pad + 15}" text-anchor="middle">{point[axis]}</text>')
        parts.append(f'<text x="{left + width / 2}" y="{height - 12}" text-anchor="middle">{axis} (log scale)</text>')
        parts.append(f'<text x="{left + 12}" y="{pad - 10}">page I/O (log)</text>')
        for op, color in colors.items():
            line = ' '.join(f'{scale_x(x):.1f},{scale_y(p["costs"][op]):.1f}' for p, x in zip(points, xs))
            parts.append(f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2"/>')
    for i, (op, color) in enumerate(colors.items()):
        parts.append(f'<text x="{60 + i * 200}" y="{height + 20}" fill="{color}">{op}</text>')
    parts.append('</svg>')
    path.write_text('\n'.join(parts), encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--plot')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        results = {'costs': measure_costs(work), 'sort_passes': measure_sort_passes(work),
                   'crossover': measure_crossover(work), 'skew': measure_skew(work),
                   'budget': measure_budget(work)}
    Path(args.out).write_text(json.dumps(results, indent=1), encoding='utf-8')
    if args.plot:
        plot(results['crossover'], Path(args.plot))


if __name__ == '__main__':
    main()
