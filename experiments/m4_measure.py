"""Module 4 measurements (Section 7) on my own dataset and workload.

    python3 -m experiments.m4_measure --out experiments/results/m4.json

Builds the dataset from experiments/m4_data.py, runs ANALYZE on every
table, then for the twenty queries in experiments/m4_workload.sql measures
estimation quality, optimized against unoptimized I/O, each rewrite rule
switched off, and the correlated-column case. Every actual number comes
from running the plan in the Module 2 executor.
"""

import argparse
import json
import statistics
import tempfile
from pathlib import Path
from dbms.execution.run import run_plan
from dbms.optimizer.cli import sql_catalog, table_columns
from dbms.optimizer.explain import format_dp_table
from dbms.optimizer.planner import ALGORITHMS, Planner
from dbms.optimizer.rewrite import ALL_RULES
from dbms.optimizer.stats import analyze, load_stats
from dbms.sql.translate import compile_sql
from dbms.storage.database import Database
from experiments.m4_data import SIZES, build

FRAMES = 16
WORKLOAD = Path(__file__).with_name('m4_workload.sql')
ALGORITHM_FRAMES = (4, 16, 64)
DP_QUERY = 8                          # q08, the four-way join
CORRELATED = {
    'q15': 'SELECT e.name, p.pid FROM Emp e JOIN Proj p ON e.eid = p.lead WHERE e.age > 65 AND e.salary > 65000;',
    'wide': ('SELECT e.name, e.pad, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid '
             'WHERE e.age > 65 AND e.salary > 65000;'),
    'star': 'SELECT * FROM Emp e JOIN Emp m ON e.mgr = m.eid WHERE e.age > 65 AND e.salary > 65000;',
}
CORRELATED_FRAMES = (4, 5, 6, 8, 12, 16)


def walk(plan: dict) -> list[dict]:
    out = [plan]
    for key in ('child', 'left', 'right'):
        if key in plan:
            out += walk(plan[key])
    return out


class Workbench:
    def __init__(self, directory: Path) -> None:
        self.db = build(directory, SIZES)
        with Database(self.db, frames=FRAMES) as db:
            for table in SIZES:
                analyze(db, table)
            self.tables, self.stats, self.catalog = table_columns(db), load_stats(db), sql_catalog(db)
        self.queries = compile_sql(WORKLOAD.read_text(encoding='utf-8'), self.catalog)

    def planner(self, frames: int = FRAMES, **options) -> Planner:
        return Planner(self.tables, self.stats, frames, **options)

    def naive(self, frames: int = FRAMES) -> Planner:
        return self.planner(frames, rules=(), reorder=False, join_algorithm='nested_loop_join', aggregate_mode='sort')

    def execute(self, plan: dict, frames: int = FRAMES):
        with Database(self.db, frames=frames) as db:
            return run_plan(db, plan, frames)

    def io(self, plan: dict, frames: int = FRAMES) -> int:
        totals = self.execute(plan, frames).totals()
        return totals['page_reads'] + totals['page_writes']


def q_error(estimated: int, actual: float) -> float:
    a = max(actual, 1)   # an empty actual result counts as 1 row, like the estimate floor
    return max(estimated, a) / min(estimated, a)


def estimation_quality(bench: Workbench) -> dict:
    per_node, per_query = [], []
    for number, logical in enumerate(bench.queries, start=1):
        plan = bench.planner().plan(logical)
        result = bench.execute(plan)
        for node, (_, label, counts) in zip(walk(plan), result.nodes):
            actual = counts['tuples_out'] / max(counts['opens'], 1)
            per_node.append({'query': number, 'node': label, 'est_rows': node['est_rows'],
                             'actual_rows': round(actual, 1), 'q_error': round(q_error(node['est_rows'], actual), 2)})
        totals = result.totals()
        per_query.append({'query': number, 'est_cost': plan['est_cost'],
                          'actual_io': totals['page_reads'] + totals['page_writes']})
    errors = [n['q_error'] for n in per_node]
    worst = max(per_node, key=lambda n: n['q_error'])
    return {'median_q_error': statistics.median(errors), 'worst': worst, 'nodes': per_node, 'cost': per_query}


def optimization_pays(bench: Workbench) -> list[dict]:
    rows = []
    for number, logical in enumerate(bench.queries, start=1):
        optimized, naive = bench.io(bench.planner().plan(logical)), bench.io(bench.naive().plan(logical))
        rows.append({'query': number, 'optimized_io': optimized, 'naive_io': naive,
                     'ratio': round(naive / max(optimized, 1), 2)})
    return rows


def rule_ablation(bench: Workbench) -> list[dict]:
    def total(rules) -> int:
        return sum(bench.io(bench.planner(rules=rules).plan(q)) for q in bench.queries)
    everything = total(ALL_RULES)
    return [{'rule': rule, 'with_rule': everything, 'without_rule': total(ALL_RULES - {rule})}
            for rule in sorted(ALL_RULES)]


def algorithms_by_frames(bench: Workbench) -> list[dict]:
    rows = []
    for number, logical in enumerate(bench.queries, start=1):
        chosen = {}
        for frames in ALGORITHM_FRAMES:
            plan = bench.planner(frames).plan(logical)
            chosen[frames] = [n['op'] for n in walk(plan) if n['op'] in ALGORITHMS]
        if any(chosen.values()):
            rows.append({'query': number, **{str(f): ops for f, ops in chosen.items()}})
    return rows


def correlated_cases(bench: Workbench) -> list[dict]:
    """Where the independence assumption picks a plan that is not the cheapest in practice."""
    rows = []
    for name, sql in CORRELATED.items():
        (logical,) = compile_sql(sql, bench.catalog)
        for frames in CORRELATED_FRAMES:
            chosen = bench.planner(frames).plan(logical)
            filters = [n for n in walk(chosen) if n['op'] == 'filter']
            actual = {algorithm: bench.io(bench.planner(frames, join_algorithm=algorithm).plan(logical), frames)
                      for algorithm in ALGORITHMS}
            filter_actual = bench.execute(chosen, frames)
            filter_rows = [c['tuples_out'] for (_, label, c) in filter_actual.nodes if label == 'filter']
            joins = [n['op'] for n in walk(chosen) if n['op'] in ALGORITHMS]
            rows.append({'query': name, 'B': frames, 'chosen': joins, 'chosen_io': bench.io(chosen, frames),
                         'est_cost': chosen['est_cost'], 'actual_by_algorithm': actual,
                         'filter_est_rows': [f['est_rows'] for f in filters], 'filter_actual_rows': filter_rows})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        bench = Workbench(Path(temp) / 'db')
        planner = bench.planner()
        planner.plan(bench.queries[DP_QUERY - 1])
        results = {
            'sizes': SIZES, 'frames': FRAMES,
            'estimation': estimation_quality(bench),
            'optimization': optimization_pays(bench),
            'rules': rule_ablation(bench),
            'algorithms_by_frames': algorithms_by_frames(bench),
            'correlated': correlated_cases(bench),
            'dp_table': format_dp_table(planner.dp_tables[0]),
        }
    Path(args.out).write_text(json.dumps(results, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
