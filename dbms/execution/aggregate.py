"""distinct and group_by, each sort based or hash based (Sections 2.4, 2.6).

Hash mode keeps the input in memory if it fits. If not, it writes what it
has to a temp file, splits that file and the rest of the input into
partitions by an FNV-1a hash of the key, then builds one table of entries
(distinct tuples or group states) per partition. A partition whose table
still does not fit is split again with a new seed, up to depth 2.
"""

from itertools import chain
from ..common.errors import ExecutionError
from ..common.schema import INT, VARCHAR, Column, resolve
from .base import END, Operator
from .context import REAL, Workspace, encode, encoded_size
from .joins import MAX_DEPTH, fnv1a
from .sort import Sort

AGGREGATES = ('count', 'sum', 'avg', 'min', 'max')
STATE_SIZE = 16   # bytes I count per aggregate in a hash table entry
MIN_PARTITION_FRAMES = 3   # a dump reader and at least 2 partition writers


class Grouping(Operator):
    """Shared driver: subclasses say what the key is and how entries fold."""

    def __init__(self, ctx, mode: str, child: Operator, key_positions: list[int]) -> None:
        super().__init__(ctx, [child])
        if mode not in ('sort', 'hash'):
            raise ExecutionError('plan', f"mode must be 'sort' or 'hash', got {mode!r}")
        self.mode = mode
        self.key_positions = key_positions
        self.key_columns = tuple(child.columns[i] for i in key_positions)
        self.files = []
        self.sort = None
        self.buffer = None

    def label(self) -> str:
        return f'{self.name} ({self.mode})'

    def key(self, row: tuple) -> tuple:
        return tuple(row[i] for i in self.key_positions)

    # Subclasses: new_entry(row), fold(entry, row), finish(key, entry) -> output row or END.
    def entry_size(self, row: tuple) -> int:
        return encoded_size(row, self.children[0].columns)

    def start(self) -> None:
        child = self.children[0]
        if self.mode == 'hash' and self.hold - 1 < MIN_PARTITION_FRAMES:
            # Too few frames to partition if the table overflows; sorting needs only one.
            self.mode = 'sort'
        if self.mode == 'sort':
            keys = [[c.qualified_name, 'asc'] for c in self.key_columns]
            if keys:
                self.sort = Sort(self.ctx, keys, child, owner=self)
                self.sort.set_limits(self.hold, self.keep)
                self.sort.open()
            else:
                child.set_limits(self.hold, self.keep)
                child.open()
            return
        child.set_limits(self.hold, max(1, self.hold - MIN_PARTITION_FRAMES))
        child.open()
        frames = self.hold - child.streaming_frames()
        self.buffer = Workspace(self.ctx, self, frames, child.columns)
        rows = self.pull(child)
        for row in rows:
            if not self.buffer.add(row):
                self.spill_input(chain([row], rows), frames)
                break
        child.close()
        if self.buffer is not None:
            self.buffer.trim()
            if self.buffer.pages_used() > self.keep:
                self.spill_input(iter(()), frames)

    def spill_input(self, rows, frames: int) -> None:
        if frames < MIN_PARTITION_FRAMES:
            raise ExecutionError('memory', f'{self.name} needs {MIN_PARTITION_FRAMES} frames to partition, has {frames}')
        columns = self.children[0].columns
        dump = self.ctx.temp_file(self).borrow(self.buffer.frames[0])
        self.files.append(dump)
        for kept in self.buffer.tuples:
            dump.write(kept, columns)
        dump.finish()
        self.buffer.close()
        self.buffer = None
        parts = self.new_partitions(frames - 1)
        self.route(parts, rows, 0)
        self.route(parts, dump.read(columns), 0)
        self.partitions = [part.finish() for part in parts]

    def new_partitions(self, count: int) -> list:
        parts = [self.ctx.temp_file(self) for _ in range(count)]
        self.files.extend(parts)
        return parts

    def route(self, parts: list, rows, seed: int) -> None:
        columns = self.children[0].columns
        for row in rows:
            index = fnv1a(encode(self.key(row), self.key_columns), seed) % len(parts)
            parts[index].write(row, columns)

    def produce(self):
        if self.mode == 'sort':
            yield from self.adjacent(self.pull(self.sort or self.children[0]))
        elif self.buffer is not None:
            table = self.build(self.buffer.tuples, None)
            yield from self.emit(table)
        else:
            for part in self.partitions:
                yield from self.from_partition(part, 1)

    def build(self, rows, space: Workspace | None) -> dict | None:
        """Fold rows into {key: entry}; None if `space` runs out of room."""
        table = {}
        for row in rows:
            key = self.key(row)
            bucket_hits = table.get(key)
            self.comparisons += 1
            if bucket_hits is None:
                if space is not None and not space.make_room(self.entry_size(row)):
                    return None
                table[key] = self.new_entry(row)
            else:
                table[key] = self.fold(bucket_hits, row)
        return table

    def emit(self, table: dict):
        for key, entry in table.items():
            row = self.finish(key, entry)
            if row is not END:
                yield row

    def from_partition(self, part, depth: int):
        columns = self.children[0].columns
        space = Workspace(self.ctx, self, self.keep - 1, columns)
        try:
            reader = part.read(columns)
            table = self.build(reader, space)
            reader.close()
            if table is not None:
                yield from self.emit(table)
                return
        finally:
            space.close()
        if depth > MAX_DEPTH:
            raise ExecutionError('memory', f'{self.name}: a partition still does not fit after {MAX_DEPTH} re-splits')
        parts = self.new_partitions(self.keep - 1)
        self.route(parts, part.read(columns), depth)
        for sub in parts:
            sub.finish()
        for sub in parts:
            yield from self.from_partition(sub, depth + 1)
            sub.delete()

    def adjacent(self, rows):
        current, entry = END, None
        for row in rows:
            key = self.key(row)
            if current is not END:
                self.comparisons += 1
            if current is END or key != current:
                if current is not END:
                    out = self.finish(current, entry)
                    if out is not END:
                        yield out
                current, entry = key, self.new_entry(row)
            else:
                entry = self.fold(entry, row)
        if current is not END:
            out = self.finish(current, entry)
            if out is not END:
                yield out
        else:
            yield from self.on_empty()

    def on_empty(self):
        return iter(())

    def min_keep(self) -> int:
        return 2 if self.mode == 'hash' else 1

    def stream_frames(self) -> int:
        if self.sort is not None:
            return self.sort.streaming_frames()
        if self.mode == 'sort':
            return self.children[0].streaming_frames()
        return len(self.buffer.frames) if self.buffer is not None else self.keep

    def stop(self) -> None:
        if self.sort is not None:
            self.sort.close()
        self.children[0].close()
        if self.buffer is not None:
            self.buffer.close()
            self.buffer = None
        for part in self.files:
            part.delete()
        self.files = []


class Distinct(Grouping):
    name = 'distinct'

    def __init__(self, ctx, mode: str, child: Operator) -> None:
        super().__init__(ctx, mode, child, list(range(len(child.columns))))
        self.columns = child.columns

    def new_entry(self, row: tuple):
        return True

    def fold(self, entry, row: tuple):
        return entry

    def finish(self, key: tuple, entry) -> tuple:
        return key


def aggregate_column(function: str, argument: str, child: Operator,
                     alias: str | None = None) -> tuple[Column, int | None]:
    if function not in AGGREGATES:
        raise ExecutionError('plan', f'unknown aggregate {function!r}; use count, sum, avg, min or max')
    if argument == '*':
        if function != 'count':
            raise ExecutionError('plan', f'{function}(*) is not allowed; only count(*)')
        return Column(None, alias or 'count_star', INT, 8), None
    index = resolve(argument, child.columns)
    source = child.columns[index]
    name = alias or f'{function}_{source.qualified_name.replace(".", "_")}'
    if function in ('sum', 'avg') and source.type != INT:
        raise ExecutionError('Type', f'{function} needs an INT column, {source.qualified_name} is {source.type}')
    if function in ('count', 'sum'):
        return Column(None, name, INT, 8), index
    if function == 'avg':
        return Column(None, name, REAL), index
    return Column(None, name, source.type, source.length), index


class GroupBy(Grouping):
    name = 'group_by'

    def __init__(self, ctx, mode: str, keys: list[str], aggregates: list, child: Operator) -> None:
        positions = [resolve(name, child.columns) for name in keys]
        super().__init__(ctx, mode, child, positions)
        for spec in aggregates:
            if not isinstance(spec, list) or len(spec) not in (2, 3):
                raise ExecutionError('plan', f'an aggregate must be [function, column] or [function, column, name], got {spec!r}')
        specs = [aggregate_column(*spec, child) if len(spec) == 2 else aggregate_column(spec[0], spec[1], child, spec[2])
                 for spec in aggregates]
        self.functions = [spec[0] for spec in aggregates]
        self.arguments = [index for _, index in specs]
        self.columns = self.key_columns + tuple(column for column, _ in specs)

    def entry_size(self, row: tuple) -> int:
        return encoded_size(self.key(row), self.key_columns) + STATE_SIZE * len(self.functions)

    def new_entry(self, row: tuple) -> tuple:
        return tuple((1, row[i], row[i], row[i]) if i is not None else (1, None, None, None)
                     for i in self.arguments)

    def fold(self, entry: tuple, row: tuple) -> tuple:
        folded = []
        for (count, total, low, high), function, i in zip(entry, self.functions, self.arguments):
            if i is None:
                folded.append((count + 1, None, None, None))
                continue
            value = row[i]
            if function in ('min', 'max'):
                self.comparisons += 1
            folded.append((count + 1,
                           total + value if function in ('sum', 'avg') else None,
                           value if function == 'min' and value < low else low,
                           value if function == 'max' and value > high else high))
        return tuple(folded)

    def finish(self, key: tuple, entry: tuple) -> tuple:
        values = []
        for (count, total, low, high), function in zip(entry, self.functions):
            values.append({'count': count, 'sum': total, 'min': low, 'max': high,
                           'avg': round(total / count, 4) if function == 'avg' else None}[function])
        return key + tuple(values)

    def on_empty(self):
        if self.key_positions:
            return
        for function in self.functions:
            if function != 'count':
                raise ExecutionError('aggregate', f'{function} of an empty input is undefined')
        yield tuple(0 for _ in self.functions)

    def produce(self):
        if self.mode == 'hash' and not self.key_positions:
            # No grouping columns: one group, folded as the input streams by.
            yield from self.adjacent(self.pull_all())
            return
        yield from super().produce()

    def pull_all(self):
        if self.buffer is not None:
            yield from self.buffer.tuples
        else:
            for part in self.partitions:
                yield from part.read(self.children[0].columns)
