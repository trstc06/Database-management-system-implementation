"""Cost model (Section 4): page reads plus writes, matched to my executor.

A plan's cost is the sum of every node's own I/O. A seqscan costs its
pages. Filter, project, rename and limit cost nothing. A join or sort
does not pay again for reading a base table, because the scan below it
already did. A join does pay M (or N) for reading an input that is
another join, as in the Section 5.1 table, even though my executor
pipelines that input for free. REPORT_M4 shows what that costs in accuracy.

Where I changed the handout's formulas to match what Module 2 does:
- sort: pass 0 runs are B - 1 pages (the input page takes a frame), and a
  sort that fits in B - 1 pages costs nothing. Its own I/O is 2M(passes - 1);
  the handout's extra + M is the input read, which the input node pays.
- sort_merge_join: sort(M) + sort(N) only. The handout's + M + N counts the
  last merge pass a second time, since my last pass feeds the join.
- hash_join: 2(M + N) for writing and reading the partitions, plus another
  2(M + N) for each level of re-partitioning when a partition is bigger
  than the B - 1 frames it can build in (up to depth 2, like Module 2).
"""

import math

MAX_DEPTH = 2


def sort_io(pages: int, frames: int, keep: int | None = None) -> int:
    """Own I/O of an external sort with `frames` to work in.

    `keep` is how many runs it may still hold open while it streams its
    output (all but one frame when it is alone; fewer under a sort merge
    join, which splits the frames between its two sorts). It merges until
    the runs fit in `keep`, and the last merge streams without writing.
    """
    run_pages = max(frames - 1, 1)
    fan_in = max(frames - 1, 2)          # Module 2 refuses to merge with fewer than 3 frames
    keep = max(run_pages if keep is None else keep, 1)
    if pages <= min(run_pages, keep):
        return 0
    runs = math.ceil(pages / run_pages)
    merges = 0
    while runs > keep:
        runs = math.ceil(runs / fan_in)
        merges += 1
    return 2 * pages * (1 + merges)


def nested_loop_extra(outer_pages: int, inner_pages: int, inner_is_scan: bool, frames: int) -> int:
    """I/O beyond reading each input once.

    A bare table inner is scanned again for every block after the first.
    Any other inner is written to a temp file once and read once per block.
    """
    blocks = math.ceil(outer_pages / max(frames - 2, 1))
    if inner_is_scan:
        return (blocks - 1) * inner_pages
    return inner_pages + blocks * inner_pages


def hash_levels(outer_pages: int, frames: int) -> int:
    """How many partitioning passes the build side needs, 1 to 1 + MAX_DEPTH."""
    partitions = max(frames - 1, 2)
    size = outer_pages / partitions
    levels = 1
    while size > frames - 1 and levels <= MAX_DEPTH:
        size /= partitions
        levels += 1
    return levels


def hash_join_extra(outer_pages: int, inner_pages: int, frames: int) -> int:
    return 2 * (outer_pages + inner_pages) * hash_levels(outer_pages, frames)


def sort_merge_extra(outer_pages: int, inner_pages: int, frames: int) -> int:
    """Both sorts, with the frames split the way Module 2 splits them."""
    left_keep = max(1, (frames - 1) // 2)
    right_keep = max(1, frames - left_keep - 1)
    return sort_io(outer_pages, frames, left_keep) + sort_io(inner_pages, frames - left_keep, right_keep)


def hash_aggregate_io(pages: int, frames: int) -> int:
    """distinct or group_by by hashing.

    Free if the input fits in the B - 1 frames of the in-memory table. If not,
    Module 2 writes those full frames to a dump file and reads them back, then
    writes and reads every page once more as partitions.
    """
    buffer = frames - 1
    return 0 if pages <= buffer else 2 * pages + 2 * buffer
