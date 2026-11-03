"""Run a plan end to end and report rows, totals and per node counters."""

from dataclasses import dataclass
from ..common.errors import ExecutionError
from ..common.schema import VARCHAR
from .base import END
from .context import Context
from .plan import build, plan_nodes

COUNTERS = ('tuples_out', 'comparisons', 'page_reads', 'page_writes')


@dataclass(frozen=True)
class Result:
    columns: tuple
    rows: list
    nodes: list          # (depth, label, counters)
    peak_frames: int

    def schema(self) -> list:
        return [[c.qualified_name if c.qualifier else c.name, c.type] + ([c.length] if c.type == VARCHAR else [])
                for c in self.columns]

    def totals(self) -> dict:
        return {name: sum(counts[name] for _, _, counts in self.nodes) for name in COUNTERS}

    def to_json(self) -> dict:
        return {'schema': self.schema(), 'rows': [list(row) for row in self.rows], 'totals': self.totals()}

    def trace(self) -> str:
        labels = ['  ' * depth + label for depth, label, _ in self.nodes]
        width = max(len(label) for label in labels) + 2
        lines = []
        for label, (_, _, c) in zip(labels, self.nodes):
            lines.append(f"{label:<{width}}tuples_out={c['tuples_out']:<8} comparisons={c['comparisons']:<9} "
                         f"reads={c['page_reads']:<7} writes={c['page_writes']}")
        return '\n'.join(lines)


def run_plan(db, plan: dict, frames: int) -> Result:
    if frames < 1 or frames != len(db.pool.frames):
        raise ExecutionError('plan', f'the pool has {len(db.pool.frames)} frames, not {frames}')
    ctx = Context(db, frames)
    try:
        root = build(ctx, plan)
        rows = []
        try:
            root.open()
            while (row := root.next()) is not END:
                rows.append(row)
        finally:
            root.close()
        problems = ctx.leftovers()
        if problems:
            raise ExecutionError('memory', '; '.join(problems))
        nodes = [(depth, op.label(), op.counters()) for depth, op in plan_nodes(root)]
        return Result(root.columns, rows, nodes, db.pool.peak_pinned)
    finally:
        ctx.remove_temp_dir()
