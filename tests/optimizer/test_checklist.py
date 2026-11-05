"""The Section 8 checklist, on a small copy of the stand-in dataset."""

import copy
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from dbms.execution.run import run_plan
from dbms.optimizer.cli import sql_catalog, table_columns
from dbms.optimizer.planner import Planner
from dbms.optimizer.rewrite import ALL_RULES
from dbms.optimizer.stats import analyze, load_stats
from dbms.sql.translate import compile_sql
from dbms.storage.database import Database
from experiments.m4_data import build

SMALL = {'Dept': 20, 'Emp': 600, 'Proj': 60, 'Site': 120}
WORKLOAD = Path(__file__).resolve().parents[2] / 'experiments' / 'm4_workload.sql'
FRAMES = 8


def physical_ops(plan: dict) -> list[str]:
    out = [plan['op']]
    for key in ('child', 'left', 'right'):
        if key in plan:
            out += physical_ops(plan[key])
    return out


class PlannerChecklist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = build(Path(tempfile.mkdtemp()) / 'db', SMALL)
        with Database(cls.db, frames=FRAMES) as db:
            for table in SMALL:
                analyze(db, table)
            cls.tables, cls.stats = table_columns(db), load_stats(db)
            cls.catalog = sql_catalog(db)
        cls.queries = compile_sql(WORKLOAD.read_text(encoding='utf-8'), cls.catalog)

    def run_plan(self, plan: dict, frames: int = FRAMES):
        with Database(self.db, frames=frames) as db:
            return run_plan(db, plan, frames)

    def planner(self, frames: int = FRAMES, **options) -> Planner:
        return Planner(self.tables, self.stats, frames, **options)

    def test_every_plan_runs_unedited_and_matches_the_naive_plan(self):
        naive = self.planner(rules=(), reorder=False, join_algorithm='nested_loop_join', aggregate_mode='sort')
        for number, logical in enumerate(self.queries, start=1):
            with self.subTest(query=number):
                optimized = self.run_plan(self.planner().plan(logical)).rows
                baseline = self.run_plan(naive.plan(logical)).rows
                if 'LIMIT' in str(logical) or logical['op'] == 'limit':
                    self.assertEqual(len(optimized), len(baseline))
                else:
                    self.assertEqual(Counter(optimized), Counter(baseline))

    def test_every_plan_runs_at_b_4_with_every_join_algorithm(self):
        for algorithm in (None, 'nested_loop_join', 'hash_join', 'sort_merge_join'):
            planner = self.planner(4, join_algorithm=algorithm)
            for number, logical in enumerate(self.queries, start=1):
                with self.subTest(algorithm=algorithm, query=number):
                    self.run_plan(planner.plan(logical), 4)

    def test_each_rule_off_still_gives_the_same_answer(self):
        logical = self.queries[3]
        expected = Counter(self.run_plan(self.planner().plan(logical)).rows)
        for rule in sorted(ALL_RULES):
            with self.subTest(rule=rule):
                plan = self.planner(rules=ALL_RULES - {rule}).plan(logical)
                self.assertEqual(Counter(self.run_plan(plan).rows), expected)

    def test_commas_and_join_on_give_the_same_physical_plan(self):
        commas, joins = compile_sql(
            'SELECT e.name FROM Emp e, Dept d, Proj p, Site s '
            'WHERE e.did = d.did AND d.did = p.did AND p.pid = s.pid AND s.city = \'Regina\';'
            'SELECT e.name FROM Emp e JOIN Dept d ON e.did = d.did JOIN Proj p ON d.did = p.did '
            'JOIN Site s ON p.pid = s.pid WHERE s.city = \'Regina\';', self.catalog)
        self.assertEqual(self.planner().plan(commas), self.planner().plan(joins))

    def test_changing_b_changes_a_join_algorithm(self):
        # On this small data one side of most joins is a page, so use a join of two big inputs.
        # REPORT_M4 shows the same effect on the full workload.
        (logical,) = compile_sql('SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid;', self.catalog)
        joins = ('nested_loop_join', 'hash_join', 'sort_merge_join')
        chosen = {frames: [op for op in physical_ops(self.planner(frames).plan(logical)) if op in joins]
                  for frames in (4, 64)}
        self.assertNotEqual(chosen[4], chosen[64])

    def test_analyze_twice_changes_nothing(self):
        before = (self.db / 'catalog.db').read_bytes()
        with Database(self.db, frames=FRAMES) as db:
            first = db.catalog.stat_records('Emp')
            analyze(db, 'Emp')
            self.assertEqual(db.catalog.stat_records('Emp'), first)
        self.assertEqual((self.db / 'catalog.db').read_bytes(), before)

    def test_no_statistics_still_gives_a_valid_plan(self):
        fresh = build(Path(tempfile.mkdtemp()) / 'db', SMALL)
        with Database(fresh, frames=FRAMES) as db:
            tables, stats = table_columns(db), load_stats(db)
        self.assertFalse(any(s.analyzed for s in stats.values()))
        for logical in self.queries[:8]:
            plan = Planner(tables, stats, FRAMES).plan(logical)
            with Database(fresh, frames=FRAMES) as db:
                run_plan(db, plan, FRAMES)

    def test_planning_does_not_change_the_logical_plan(self):
        logical = self.queries[7]
        kept = copy.deepcopy(logical)
        self.planner().plan(logical)
        self.assertEqual(logical, kept)


if __name__ == '__main__':
    unittest.main()
