"""Block nested loop, sort merge and Grace hash joins (Sections 2.7 to 2.9)."""

from ..common.errors import ExecutionError
from ..common.schema import check_unique, resolve
from ..ra.conditions import bind_condition
from ..ra.parser import parse_condition
from .base import END, Operator
from .context import Workspace, encode
from .pipeline import SeqScan
from .sort import Sort

FNV_OFFSET = 0xcbf29ce484222325
FNV_PRIME = 0x100000001b3
MAX_DEPTH = 2


def fnv1a(data: bytes, seed: int) -> int:
    """64 bit FNV-1a. A different seed gives a different hash function."""
    value = FNV_OFFSET ^ (seed * FNV_PRIME)
    for byte in data:
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return value


def equality_keys(condition: str, left, right) -> tuple[list[int], list[int]]:
    """'R.b = S.b and R.c = S.d' -> key positions in the left and right schemas."""
    node = parse_condition(condition)
    pairs = []
    def walk(node) -> None:
        if node.kind == 'And':
            walk(node.children[0])
            walk(node.children[1])
            return
        if node.kind != 'Eq' or any(child.kind != 'Attr' for child in node.children):
            raise ExecutionError('plan', f'this join needs column = column conditions joined by and, got {condition!r}')
        pairs.append((node.children[0].value, node.children[1].value))
    walk(node)
    left_keys, right_keys = [], []
    for a, b in pairs:
        if any(c.qualified_name == a or c.name == a for c in right.columns) and \
                not any(c.qualified_name == a or c.name == a for c in left.columns):
            a, b = b, a
        li, ri = resolve(a, left.columns), resolve(b, right.columns)
        if left.columns[li].type != right.columns[ri].type:
            raise ExecutionError('Type', f'cannot compare {left.columns[li].type} with {right.columns[ri].type}')
        left_keys.append(li)
        right_keys.append(ri)
    return left_keys, right_keys


class Join(Operator):
    def min_keep(self) -> int:
        return 3   # every join streams with at least one frame per input plus one of its own

    def __init__(self, ctx, condition: str, left: Operator, right: Operator) -> None:
        super().__init__(ctx, [left, right])
        self.condition = condition
        self.columns = left.columns + right.columns
        check_unique(self.columns)

    def stop(self) -> None:
        for child in self.children:
            child.close()


class NestedLoopJoin(Join):
    name = 'nested_loop_join'

    def __init__(self, ctx, condition: str, left: Operator, right: Operator) -> None:
        super().__init__(ctx, condition, left, right)
        # No condition means every pair matches: a cross product.
        self.test = bind_condition(parse_condition(condition), self.columns) if condition else (lambda row: True)
        self.inner_file = None
        self.block = None

    def start(self) -> None:
        outer, inner = self.children
        if not isinstance(inner, SeqScan):
            inner.set_limits(self.hold, self.hold - 1)
            inner.open()
            self.inner_file = self.ctx.temp_file(self)
            for row in self.pull(inner):
                self.inner_file.write(row, inner.columns)
            self.inner_file.finish()
            inner.close()
        outer.set_limits(self.hold, self.keep - 2)
        outer.open()
        frames = self.keep - 1 - outer.streaming_frames()
        if frames < 1:
            raise ExecutionError('memory', f'nested_loop_join needs at least 3 frames, has {self.keep}')
        self.block = Workspace(self.ctx, self, frames, outer.columns)

    def inner_rows(self):
        inner = self.children[1]
        if self.inner_file is not None:
            yield from self.inner_file.read(inner.columns)
            return
        inner.close()
        inner.set_limits(1, 1)
        inner.open()
        yield from self.pull(inner)
        inner.close()

    def produce(self):
        outer_rows = self.pull(self.children[0])
        pending = next(outer_rows, END)
        while pending is not END:
            self.block.clear()
            while pending is not END and self.block.add(pending):
                pending = next(outer_rows, END)
            for inner_row in self.inner_rows():
                for outer_row in self.block.tuples:
                    self.comparisons += 1
                    row = outer_row + inner_row
                    if self.test(row):
                        yield row

    def stream_frames(self) -> int:
        return len(self.block.frames) + 1 + self.children[0].streaming_frames()

    def stop(self) -> None:
        super().stop()
        if self.block is not None:
            self.block.close()
            self.block = None
        if self.inner_file is not None:
            self.inner_file.delete()
            self.inner_file = None


class SortMergeJoin(Join):
    name = 'sort_merge_join'

    def __init__(self, ctx, condition: str, left: Operator, right: Operator) -> None:
        super().__init__(ctx, condition, left, right)
        self.left_keys, self.right_keys = equality_keys(condition, left, right)
        keys = lambda columns, positions: [[columns[i].qualified_name, 'asc'] for i in positions]
        self.sorts = [Sort(ctx, keys(left.columns, self.left_keys), left, owner=self),
                      Sort(ctx, keys(right.columns, self.right_keys), right, owner=self)]
        self.run = None
        self.spill = None

    def start(self) -> None:
        left_keep = max(1, (self.keep - 1) // 2)
        self.sorts[0].set_limits(self.hold, left_keep)
        self.sorts[0].open()
        self.sorts[1].set_limits(self.hold - self.sorts[0].streaming_frames(), self.keep - left_keep - 1)
        self.sorts[1].open()
        self.run = Workspace(self.ctx, self, 1, self.children[1].columns)

    def key(self, row: tuple, positions: list[int]) -> tuple:
        return tuple(row[i] for i in positions)

    def compare(self, a: tuple, b: tuple) -> int:
        self.comparisons += 1
        return (a > b) - (a < b)

    def collect_run(self, first: tuple, key: tuple, rights):
        """Buffer the right run with this key; spill it if it outgrows the frame."""
        columns = self.children[1].columns
        self.run.clear()
        self.run.add(first)
        spill = None
        following = END
        for row in rights:
            if self.compare(self.key(row, self.right_keys), key) != 0:
                following = row
                break
            if spill is None and self.run.add(row):
                continue
            if spill is None:
                spill = self.ctx.temp_file(self).borrow(self.run.frames[0])
                for kept in self.run.tuples:
                    spill.write(kept, columns)
                self.run.clear()
            spill.write(row, columns)
        if spill is None:
            return list(self.run.tuples), following
        self.spill = spill.finish()
        self.run.close()   # the spill is read back with this frame instead
        return None, following

    def produce(self):
        lefts = self.pull(self.sorts[0])
        rights = self.pull(self.sorts[1])
        r, s = next(lefts, END), next(rights, END)
        while r is not END and s is not END:
            r_key, s_key = self.key(r, self.left_keys), self.key(s, self.right_keys)
            order = self.compare(r_key, s_key)
            if order < 0:
                r = next(lefts, END)
            elif order > 0:
                s = next(rights, END)
            else:
                run, s = self.collect_run(s, s_key, rights)
                while r is not END and self.compare(self.key(r, self.left_keys), s_key) == 0:
                    matches = run if run is not None else self.spill.read(self.children[1].columns)
                    for match in matches:
                        yield r + match
                    r = next(lefts, END)
                if self.spill is not None:
                    self.spill.delete()
                    self.spill = None
                    self.run = Workspace(self.ctx, self, 1, self.children[1].columns)

    def stream_frames(self) -> int:
        return sum(sort.streaming_frames() for sort in self.sorts) + 1

    def stop(self) -> None:
        for sort in self.sorts:
            sort.close()
        if self.run is not None:
            self.run.close()
            self.run = None
        if self.spill is not None:
            self.spill.delete()
            self.spill = None


class HashJoin(Join):
    """Grace hash join; the left input is the build side."""
    name = 'hash_join'

    def __init__(self, ctx, condition: str, left: Operator, right: Operator) -> None:
        super().__init__(ctx, condition, left, right)
        self.left_keys, self.right_keys = equality_keys(condition, left, right)
        self.left_key_columns = tuple(left.columns[i] for i in self.left_keys)
        self.files = []
        self.partition_pages = []
        self.overflows = []

    def bucket(self, row: tuple, positions: list[int], seed: int, count: int) -> int:
        key = tuple(row[i] for i in positions)
        return fnv1a(encode(key, self.left_key_columns), seed) % count

    def partition(self, rows, columns, positions, seed: int, count: int) -> list:
        files = [self.ctx.temp_file(self) for _ in range(count)]
        self.files.extend(files)
        for row in rows:
            files[self.bucket(row, positions, seed, count)].write(row, columns)
        return [part.finish() for part in files]

    def start(self) -> None:
        left, right = self.children
        left.set_limits(self.hold, self.hold - 2)
        left.open()
        count = min(self.hold - left.streaming_frames(), self.hold - 1)
        if count < 2:
            raise ExecutionError('memory', f'hash_join needs at least 3 frames, has {self.hold}')
        self.left_parts = self.partition(self.pull(left), left.columns, self.left_keys, 0, count)
        left.close()
        right.set_limits(self.hold, self.hold - count)
        right.open()
        self.right_parts = self.partition(self.pull(right), right.columns, self.right_keys, 0, count)
        right.close()
        self.partition_pages = [part.pages for part in self.left_parts]
        if self.keep < 3:
            raise ExecutionError('memory', f'hash_join needs 3 frames to probe, has {self.keep}')

    def produce(self):
        for left_part, right_part in zip(self.left_parts, self.right_parts):
            yield from self.join_pair(left_part, right_part, 1)

    def join_pair(self, left_part, right_part, depth: int):
        left, right = self.children
        table = Workspace(self.ctx, self, self.keep - 1, left.columns)
        try:
            reader = left_part.read(left.columns)
            fits = all(table.add(row) for row in reader)
            reader.close()
            if fits:
                yield from self.probe(table.tuples, right_part, depth)
                return
        finally:
            table.close()
        if depth <= MAX_DEPTH and left_part.pages > 1:
            self.overflows.append(('repartition', depth, left_part.pages))
            count = self.keep - 1
            lefts = self.partition(left_part.read(left.columns), left.columns, self.left_keys, depth, count)
            rights = self.partition(right_part.read(right.columns), right.columns, self.right_keys, depth, count)
            for a, b in zip(lefts, rights):
                yield from self.join_pair(a, b, depth + 1)
                a.delete()
                b.delete()
            return
        self.overflows.append(('nested_loop', depth, left_part.pages))
        yield from self.nested_pair(left_part, right_part)

    def probe(self, build: list, right_part, depth: int):
        count = max(1, len(build))
        buckets = [[] for _ in range(count)]
        for row in build:
            buckets[self.bucket(row, self.left_keys, MAX_DEPTH + depth, count)].append(row)
        for row in right_part.read(self.children[1].columns):
            key = tuple(row[i] for i in self.right_keys)
            for candidate in buckets[self.bucket(row, self.right_keys, MAX_DEPTH + depth, count)]:
                self.comparisons += 1
                if tuple(candidate[i] for i in self.left_keys) == key:
                    yield candidate + row

    def nested_pair(self, left_part, right_part):
        left, right = self.children
        block = Workspace(self.ctx, self, self.keep - 2, left.columns)
        try:
            lefts = left_part.read(left.columns)
            pending = next(lefts, END)
            while pending is not END:
                block.clear()
                while pending is not END and block.add(pending):
                    pending = next(lefts, END)
                for row in right_part.read(right.columns):
                    key = tuple(row[i] for i in self.right_keys)
                    for candidate in block.tuples:
                        self.comparisons += 1
                        if tuple(candidate[i] for i in self.left_keys) == key:
                            yield candidate + row
        finally:
            block.close()

    def stream_frames(self) -> int:
        return self.keep

    def stop(self) -> None:
        super().stop()
        for part in self.files:
            part.delete()
        self.files = []
