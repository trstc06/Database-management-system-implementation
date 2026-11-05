"""The iterator interface every operator follows.

Subclasses fill in `start` (open work), `produce` (a generator of output
tuples) and `stop` (cleanup). This class times every call so reads and
writes can be charged: an operator's own I/O is everything that happened
during its calls minus what its plan children did during theirs.

Frame limits: `hold` is how many frames the operator may hold during
open, `keep` how many afterwards. Both count the pages its inputs pin.
If `keep` is below what the operator needs to stream (`min_keep`), it does
all its work inside open with `hold` frames, writes its output to a temp
file and streams that back with one frame.
"""

from ..common.errors import ExecutionError
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
        self.opens = 0     # a nested loop join's inner input is opened once per block
        self.comparisons = 0
        self.total_reads = self.total_writes = 0
        self.stream = None
        self.is_open = False
        self.spilled = None    # temp file of the output when it had to be materialized

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

    def min_keep(self) -> int:
        """Fewest frames this operator needs to stream its output."""
        return 0

    def open(self) -> None:
        def work():
            if self.keep < self.min_keep():
                self.materialize()
            else:
                self.start()
                self.stream = self.produce()
        self.timed(work)
        self.is_open = True
        self.opens += 1

    def next(self) -> tuple | None:
        def work():
            row = next(self.stream, END)
            if row is not END:
                self.tuples_out += 1
            return row
        return self.timed(work)

    def materialize(self) -> None:
        if self.keep < 1 or self.hold - 1 < self.min_keep():
            raise ExecutionError('memory', f'{self.label()} needs {self.min_keep() + 1} frames, has {self.hold}')
        streaming_limit, self.keep = self.keep, self.hold - 1   # one frame stays free for the output writer
        self.start()
        out = self.ctx.temp_file(self)
        rows = self.produce()
        for row in rows:
            out.write(row, self.columns)
        rows.close()
        self.spilled = out.finish()
        self.stop()
        self.keep = streaming_limit
        self.stream = self.spilled.read(self.columns)

    def close(self) -> None:
        if not self.is_open:
            return
        def work():
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            self.stop()
            if self.spilled is not None:
                self.spilled.delete()
                self.spilled = None
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
        return 1 if self.spilled is not None else self.stream_frames()

    def stream_frames(self) -> int:
        return max((child.streaming_frames() for child in self.children), default=0)

    def counters(self) -> dict:
        reads = self.total_reads - sum(child.total_reads for child in self.children)
        writes = self.total_writes - sum(child.total_writes for child in self.children)
        return {'tuples_out': self.tuples_out, 'comparisons': self.comparisons,
                'page_reads': reads, 'page_writes': writes, 'opens': self.opens}
