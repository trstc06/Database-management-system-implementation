"""Replacement policies for the buffer pool.

A policy sees the frame array. `on_access` runs on every fetch, hit or
miss. `choose_victim` returns a frame index whose pin count is zero, or
None when every frame is pinned. An empty frame is always a candidate, so
the first B misses fill the pool without evicting anything.
"""

from collections import OrderedDict


class LRUPolicy:
    """Victim is the least recently used unpinned frame; empty frames first."""

    name = 'lru'

    def __init__(self, frame_count: int) -> None:
        self.order = OrderedDict()  # frame index -> None, least recent first

    def on_access(self, index: int) -> None:
        self.order.pop(index, None)
        self.order[index] = None

    def choose_victim(self, frames: list) -> int | None:
        for frame in frames:
            if frame.key is None:
                return frame.index
        for index in self.order:
            if frames[index].pin_count == 0:
                return index
        return None


class ClockPolicy:
    """Section 5.2: reference bits and a hand sweeping the frame circle."""

    name = 'clock'

    def __init__(self, frame_count: int) -> None:
        self.ref = [0] * frame_count
        self.hand = 0

    def on_access(self, index: int) -> None:
        self.ref[index] = 1

    def advance(self, frame_count: int) -> None:
        self.hand = (self.hand + 1) % frame_count

    def choose_victim(self, frames: list) -> int | None:
        count = len(frames)
        swept = 0
        while True:
            frame = frames[self.hand]
            if frame.pin_count > 0:
                self.advance(count)
                swept += 1
            elif self.ref[self.hand] == 1:
                self.ref[self.hand] = 0
                self.advance(count)
                swept += 1
            else:
                victim = self.hand
                self.advance(count)
                return victim
            if swept > 2 * count:
                return None


POLICIES = {'lru': LRUPolicy, 'clock': ClockPolicy}
