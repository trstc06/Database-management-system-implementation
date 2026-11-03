# Module 2 design

Code: `dbms/execution/`. Command: `bin/exec` (run it as `./bin/exec`, since `exec` is a shell builtin). Tests: `tests/execution/`.

## Memory rule

Every page an operator holds is a pinned frame in the one buffer pool of B frames, so the pool itself stops a plan from going over B. Working memory (a sort run, a hash table, a join block) is a set of frames the operator reserves from the pool. It never holds more tuples than would fit in those pages: I add up each tuple's encoded size plus its 4 byte slot, the same way a real page fills up.

Each operator gets two numbers from its parent: how many frames it may hold during `open`, and how many it may keep after `open` while it streams. Both include the pages pinned by the inputs it is reading. The root gets B and B.

- seqscan holds 1. filter, project and limit hold nothing of their own and pass their numbers down. union_all opens its right input only after the left one is done.
- A blocking operator lets its input keep all but 1 frame while it reads it, uses the rest for its first phase, and can use all of its frames once the input is closed. After `open` it shrinks to what it is allowed to keep.
- sort_merge_join, intersect and except split the frames between their two sorts and keep 1 frame for themselves.
- hash_join uses P = (frames it may hold) minus (frames its left input holds) partitions, and uses the same P for the right side.

At B = 4 a join of two tables runs, because the two inputs are read one after the other.

## Temporary files

Runs and partitions are files in `DIR/.tmp/`. Writing one uses 1 frame: I fill a page and write it when it is full. Reading uses 1 frame. These reads and writes are charged to the operator that made the file. Files are deleted on `close`, and after every plan the runner checks that `.tmp` is empty and no frame is pinned.

## Counters

Each operator counts its own comparisons. For reads and writes I measure everything that happened while the operator was running and subtract what its children did.

## Sort

Pass 0 runs are as many pages as the free frames allow. Because the input page takes a frame, a sort at the root over a table makes runs of B − 1 pages, not B. So the Section 2.5 example (N = 8, B = 3) gives 4 runs of 2 pages, 3 passes, 24 reads and 16 writes in my version (plus 1 read for the table's directory page). Merges take B − 1 runs at a time and a leftover single run is carried forward without being rewritten. The last merge streams to the parent.

## Joins

- **Block nested loop:** the block is (frames) minus 1 for the inner page minus what the outer input holds, which is B − 2 over two tables. If the inner input is not a table, I write it to a temp file once at `open` so I can read it again.
- **Sort merge:** duplicate keys follow the pseudocode in 2.8. The run of equal keys on the right goes into my 1 frame. If it doesn't fit, I write it to a temp file and read it back for each matching left tuple.
- **Hash join:** FNV-1a hashing with a different seed per level, never Python's `hash()`. If a left partition is bigger than its build frames, I partition that pair again with a new seed, up to depth 2. After that I fall back to block nested loop on that pair.

## Distinct and group by

Sort mode uses my sort and checks neighbours. Hash mode keeps everything in memory if it fits. If it doesn't, it writes what it has to a temp file, splits that file and the rest of the input into partitions and handles one partition at a time.
