"""The iterator interface every operator follows.

Subclasses fill in `start` (open work), `produce` (a generator of output
tuples) and `stop` (cleanup). This class times every call so reads and
writes can be charged: an operator's own I/O is everything that happened
during its calls minus what its plan children did during theirs.

Frame limits: `hold` is how many frames the operator may hold during
open, `keep` how many afterwards. Both count the pages its inputs pin.
"""

from ..common.schema import Column

END = None


class Operator:
    name = 'operator'

    def __init__(self, ctx, children: list['Operator']) -> None:
        self.ctx = ctx
        self.children = children
        self.columns: tuple[Column, ...] = ()
        self.hold = self.keep = ctx.frames
        self.tuples_out = 0
        self.comparisons = 0
        self.total_reads = self.total_writes = 0
        self.stream = None
        self.is_open = False

    # Frame limits flow from parent to child before open.
    def set_limits(self, hold: int, keep: int) -> None:
        self.hold, self.keep = hold, keep

    def label(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.label()

    def timed(self, work):
        reads, writes = self.ctx.io()
        try:
            return work()
        finally:
            after_reads, after_writes = self.ctx.io()
            self.total_reads += after_reads - reads
            self.total_writes += after_writes - writes

    def open(self) -> None:
        def work():
            self.start()
            self.stream = self.produce()
        self.timed(work)
        self.is_open = True

    def next(self) -> tuple | None:
        def work():
            row = next(self.stream, END)
            if row is not END:
                self.tuples_out += 1
            return row
        return self.timed(work)

    def close(self) -> None:
        if not self.is_open:
            return
        def work():
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            self.stop()
        self.timed(work)
        self.is_open = False

    def start(self) -> None:
        pass

    def produce(self):
        return iter(())

    def stop(self) -> None:
        pass

    def pull(self, child: 'Operator'):
        """Yield every tuple of `child`; the caller opens and closes it."""
        while (row := child.next()) is not END:
            yield row

    def streaming_frames(self) -> int:
        """Frames this operator holds while it is being pulled from."""
        return max((child.streaming_frames() for child in self.children), default=0)

    def counters(self) -> dict:
        reads = self.total_reads - sum(child.total_reads for child in self.children)
        writes = self.total_writes - sum(child.total_writes for child in self.children)
        return {'tuples_out': self.tuples_out, 'comparisons': self.comparisons,
                'page_reads': reads, 'page_writes': writes}
