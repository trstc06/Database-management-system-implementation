"""External merge sort (Section 2.5).

Pass 0 fills the free frames with input tuples, sorts them and writes a
run. Merge passes take (frames - 1) runs at a time with one output frame,
and a single leftover run is carried forward untouched. The last merge
streams to the parent, so it may merge at most `keep` runs.
"""

import heapq
from functools import cmp_to_key
from ..common.errors import ExecutionError
from ..common.schema import resolve
from .base import Operator
from .context import Workspace

MIN_MERGE_FRAMES = 3


def key_positions(keys: list, columns) -> list[tuple[int, bool]]:
    """[["R.a", "asc"], ...] -> [(index, descending), ...]."""
    positions = []
    for name, direction in keys:
        if direction not in ('asc', 'desc'):
            raise ExecutionError('plan', f"sort direction must be 'asc' or 'desc', got {direction!r}")
        positions.append((resolve(name, columns), direction == 'desc'))
    return positions


class Comparer:
    """Compares tuples on the sort keys and counts every comparison."""

    def __init__(self, owner: Operator, positions: list[tuple[int, bool]]) -> None:
        self.owner = owner
        self.positions = positions

    def __call__(self, a: tuple, b: tuple) -> int:
        self.owner.comparisons += 1
        for index, descending in self.positions:
            x, y = a[index], b[index]
            if x != y:
                result = -1 if x < y else 1
                return -result if descending else result
        return 0


class Sort(Operator):
    name = 'sort'

    def __init__(self, ctx, keys: list, child: Operator, *, owner: Operator | None = None) -> None:
        super().__init__(ctx, [child])
        self.columns = child.columns
        self.compare = Comparer(owner or self, key_positions(keys, child.columns))
        self.keys = keys
        self.runs = []
        self.memory = None
        self.passes = 0

    def start(self) -> None:
        child = self.children[0]
        child.set_limits(self.hold, self.hold - 1)
        child.open()
        frames = self.hold - child.streaming_frames()
        if frames < 1:
            raise ExecutionError('memory', f'sort has no frame left for pass 0 (limit {self.hold})')
        self.memory = Workspace(self.ctx, self, frames, self.columns)
        self.passes = 1
        for row in self.pull(child):
            if not self.memory.add(row):
                self.write_run()
                if not self.memory.add(row):
                    raise ExecutionError('memory', 'a tuple is larger than a page')
        child.close()
        if not self.runs and self.memory.pages_used() <= self.keep:
            self.sort_in_memory()
            return
        if self.memory.tuples:
            self.write_run()
        self.memory.close()
        self.memory = None
        self.merge_down()

    def sort_in_memory(self) -> None:
        self.memory.tuples.sort(key=cmp_to_key(self.compare))
        self.memory.trim()

    def write_run(self) -> None:
        rows = sorted(self.memory.tuples, key=cmp_to_key(self.compare))
        run = self.ctx.temp_file(self).borrow(self.memory.frames[0])
        for row in rows:
            run.write(row, self.columns)
        self.runs.append(run.finish())
        self.memory.clear()

    def merge_down(self) -> None:
        fan_in = self.hold - 1
        final_fan_in = min(fan_in, self.keep)
        if fan_in < MIN_MERGE_FRAMES - 1 and len(self.runs) > final_fan_in:
            raise ExecutionError('memory', f'sort needs at least {MIN_MERGE_FRAMES} frames to merge, has {self.hold}')
        if final_fan_in < 1 and self.runs:
            raise ExecutionError('memory', 'sort needs 1 frame to stream its output')
        while len(self.runs) > final_fan_in:
            self.passes += 1
            merged = []
            for begin in range(0, len(self.runs), fan_in):
                group = self.runs[begin:begin + fan_in]
                merged.append(group[0] if len(group) == 1 else self.merge_to_file(group))
            self.runs = merged
        if self.runs:
            self.passes += 1

    def merge_to_file(self, group: list) -> object:
        out = self.ctx.temp_file(self)
        for row in self.merged(group):
            out.write(row, self.columns)
        for run in group:
            run.delete()
        return out.finish()

    def merged(self, group: list):
        sort_key = cmp_to_key(self.compare)
        streams = [run.read(self.columns) for run in group]
        try:
            yield from heapq.merge(*streams, key=sort_key)
        finally:
            for stream in streams:
                stream.close()

    def produce(self):
        if self.memory is not None:
            yield from self.memory.tuples
            return
        yield from self.merged(self.runs)

    def streaming_frames(self) -> int:
        return len(self.memory.frames) if self.memory is not None else len(self.runs)

    def stop(self) -> None:
        self.children[0].close()
        if self.memory is not None:
            self.memory.close()
            self.memory = None
        for run in self.runs:
            run.delete()
        self.runs = []
