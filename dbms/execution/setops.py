"""union, intersect and except (Section 2.10).

union is union_all followed by a hash distinct. intersect and except sort
both inputs on every column, like a sort merge join, and walk them in step,
so neither side is ever held whole in memory.
"""

from .aggregate import Distinct
from .base import END, Operator
from .pipeline import UnionAll, check_compatible
from .sort import Sort


class Union(Operator):
    name = 'union'

    def __init__(self, ctx, left: Operator, right: Operator) -> None:
        super().__init__(ctx, [left, right])
        self.inner = Distinct(ctx, 'hash', UnionAll(ctx, left, right))
        self.columns = self.inner.columns

    def start(self) -> None:
        self.inner.set_limits(self.hold, self.keep)
        self.inner.open()

    def produce(self):
        yield from self.pull(self.inner)

    def stream_frames(self) -> int:
        return self.inner.streaming_frames()

    def min_keep(self) -> int:
        return 2

    def stop(self) -> None:
        self.inner.close()

    def counters(self) -> dict:
        counts = super().counters()
        counts['comparisons'] = self.inner.comparisons
        return counts


class SortedSetOp(Operator):
    keep_matches = True

    def __init__(self, ctx, left: Operator, right: Operator) -> None:
        super().__init__(ctx, [left, right])
        check_compatible(self.name, left, right)
        self.columns = left.columns
        self.sorts = [Sort(ctx, [[c.qualified_name, 'asc'] for c in side.columns], side, owner=self)
                      for side in (left, right)]

    def start(self) -> None:
        left_keep = max(1, self.keep // 2)
        self.sorts[0].set_limits(self.hold, left_keep)
        self.sorts[0].open()
        self.sorts[1].set_limits(self.hold - self.sorts[0].streaming_frames(), self.keep - left_keep)
        self.sorts[1].open()

    def compare(self, a: tuple, b: tuple) -> int:
        self.comparisons += 1
        return (a > b) - (a < b)

    def produce(self):
        lefts, rights = self.pull(self.sorts[0]), self.pull(self.sorts[1])
        r, s = next(lefts, END), next(rights, END)
        previous = END
        while r is not END:
            if previous is not END and self.compare(r, previous) == 0:
                r = next(lefts, END)
                continue
            while s is not END and self.compare(s, r) < 0:
                s = next(rights, END)
            found = s is not END and s == r
            if found == self.keep_matches:
                yield r
            previous = r
            r = next(lefts, END)

    def stream_frames(self) -> int:
        return sum(sort.streaming_frames() for sort in self.sorts)

    def min_keep(self) -> int:
        return 2

    def stop(self) -> None:
        for sort in self.sorts:
            sort.close()


class Intersect(SortedSetOp):
    name = 'intersect'


class Except(SortedSetOp):
    name = 'except'
    keep_matches = False
