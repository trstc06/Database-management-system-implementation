# Architecture

This is a small database system in Python 3.13 with only the standard library. SQL goes in and rows come out. `./bin/dbms --db DIR` opens a prompt. Each module also still has its own tool (`ra`, `storage`, `exec`, `sql`, `opt`).

## The whole system

```
SQL text
  |  dbms/shell     splits statements, runs shell commands, opens the database
  v
[M3] dbms/sql        lexer -> parser -> name resolution and checks -> translator
  |  logical plan (JSON dict)
  v
[M4] dbms/optimizer  rewrite rules -> estimates from statistics -> cost -> DP join order -> algorithms
  |  physical plan (JSON dict with est_rows, est_pages, est_cost)
  v
[M2] dbms/execution  operator tree, open / next / close, B frames from one buffer pool
  |  page requests (fetch, unpin, reserve frame)
  v
[M1] dbms/storage    buffer pool -> heap files -> 4096 byte slotted pages on disk
     catalog.db: tables, columns, statistics (read by M3 and M4, written by LOAD and ANALYZE)

dbms/ra      my Bonus 1 engine as a library: the condition parser and printer every module uses
dbms/common  errors and column types shared by all of them
```

The shell adds no logic of its own. `SELECT` runs `compile_sql`, then `Planner.plan`, then `run_plan`. `EXPLAIN` stops after planning. `EXPLAIN ANALYZE` runs the plan and prints estimated and actual rows next to each node. `ANALYZE table` calls Module 4's `analyze`. `LOAD table FROM file` calls Module 1's CSV loader. `SET frames = n` changes B for the statements after it. Every statement opens the database with a cold pool of B frames, so the page I/O it reports doesn't depend on what ran before it. `--workload` runs a file of queries and prints, for each one, the row count, page I/O, wall time, and estimated against actual rows at the root.

## Interfaces between modules

| From → to | What crosses | Rules both sides follow |
|---|---|---|
| M3 → M4 | Logical plan: nested dicts with `op` and `child` or `left`/`right` | Only logical names (scan, select, project, join, cross, rename, group_by, distinct, sort, limit, union, union_all, intersect, except). Every table column is fully qualified (`e.age`). Conditions are strings in the Bonus 1 condition syntax. Canonical order, nothing moved. |
| M4 → M2 | Physical plan: the same dict shape with physical names | seqscan (with `alias`), filter, project (with `aliases`), rename, sort, distinct and group_by (with `mode`), the three joins, the set operators, limit. Aggregates are `[function, column, name]`. Extra `est_` fields are allowed and ignored. M2 checks every node before it opens anything. |
| M2 → M1 | Calls into the buffer pool and heap files | `heap.scan()` pins one page at a time. Working memory is `pool.reserve_frame()`, so the pool itself stops a plan from going over B. Temp files are separate files read and written page by page and counted in the same I/O totals. |
| M1 → M3 | Table schemas from `catalog.db` | Column names and types. M3 resolves every name against them. |
| M1 → M4 | n_tuples and n_pages from TABLE records, plus STATS and BUCKET records | Written by `LOAD` and `ANALYZE`. With no statistics, M4 uses documented defaults. |
| ra → M2, M3, M4 | `parse_condition`, `condition_to_text`, `bind_condition` | One condition syntax everywhere, so a condition M3 prints is exactly what M2 parses. |
| everyone → user | `DbError` | One error format: `error: <category>: <message> at line L, column C`. |

## What I would do differently

**I would decide the memory rule and the cost model together, in week one.** I wrote Module 2's frame rule thinking about one operator at a time. It only broke in Module 4, when the optimizer stacked a group by over a join at B = 4, and I had to add a "materialize when you can't stream" fallback late. The cost model then had to copy Module 2's odd frame splits (the two sorts in a sort merge join, the dump in hash grouping) to stay accurate. If both had been designed as one thing, the formulas would be simpler and match the executor without special cases.

**I would make sort runs B pages, not B − 1.** My sort counts the input page as one of its frames, so its runs are a page short. That one decision is behind the different numbers for the 2.5 example, the extra passes in REPORT_M2, and a special case in the cost model. Building runs inside the input's own frames would have matched the textbook formulas.

**I would type the plans.** The plans that pass between modules are plain dicts, and some field names (`n` for limit, `aliases`, the third aggregate element) are my guesses where the handout is silent. Frozen dataclasses with one shared schema, checked in one place, would catch a wrong field at the boundary instead of deep inside an operator.

**I would count pipelined inputs as free everywhere.** I kept the handout's convention that a join pays M again for an intermediate outer, so that Section 5.1 reproduces. My executor doesn't do that, so my estimates are too high by exactly that much. I'd rather reproduce the table by explaining the difference than by copying the convention.

**I would keep better statistics.** My two worst estimate errors have known fixes: a union estimate that ignores duplicate removal, and ndv capping that also shrinks join keys the filter never touched. The correlated-columns failure needs statistics on pairs of columns. I would plan for these from the start instead of finding them in the report.

## Reading

- **Hellerstein, Stonebraker and Hamilton, "Architecture of a Database System".** Its picture of a query going through a parser, a rewriter, an optimizer and an executor, with the catalog shared by all of them, is the shape of this system. It's why the rewrite rules run inside the optimizer before costing, and why the catalog is the one place statistics live.
- **CMU 15-445 (Andy Pavlo).** The query execution lectures present the iterator model as one choice next to materialization. That's how I came to treat materializing as a fallback for when an operator can't stream in the frames it's given, rather than as a mistake.
