# Module 4 design

Code: `dbms/optimizer/`. Command: `bin/opt`. Tests: `tests/optimizer/`.

## Statistics in the catalog

`opt analyze` counts the tuples and record widths in one scan, then sorts each column with my Module 2 external sort and walks the sorted values once. Nothing is sampled. Two new record kinds go in `catalog.db`:

| Kind | Record | Fields |
|---|---|---|
| 3 | STATS | table, column, ndv, total_bytes, min, max |
| 4 | BUCKET | table, column, index, low, high, count, ndv |

n_tuples and n_pages stay in the TABLE record from Module 1. width(T) is the sum over the columns of total_bytes / n_tuples. min, max, low and high are stored as text and turned back into values using the column's type.

The histogram cuts at every n/k-th value (k from `--buckets`, default 20), but it never splits a run of equal values across two buckets. That way "the bucket holding v" is always one bucket. The cost is fewer than k buckets when one value is very common.

If the new records are the same as the stored ones, ANALYZE writes nothing, so running it twice leaves `catalog.db` byte for byte the same.

## Selectivity

I use the formulas in Section 2 with these choices:

- `c < v` and `c > v` use the linear fraction alone, so `age > 60` on ages 20 to 70 is 10/50, as in the example. `<=` and `>=` add sel(c = v).
- Without a histogram but with min and max, a range uses (v − min) / (max − min).
- A value outside every bucket has selectivity 0, which the "never below 1 row" rule turns into 1 row.
- Column = column is 1 / max(ndv), on one table or across a join. Any other two-column predicate is **0.1**.
- **Defaults with no statistics:** = keeps 0.1, a range keeps 1/3, and width comes from the schema (INT 4 bytes, VARCHAR(n) half full). n_tuples and n_pages still come from the TABLE record, which the loader keeps up to date.
- After a filter or join, each column's ndv is capped at the new row count, and after A = B both sides take the smaller ndv. group_by gives min(rows, product of the keys' ndv), and with no keys exactly 1 row. union adds the two sides (an upper bound), intersect takes the smaller and except the left.

Output pages are ceil(rows × width / 4096), except a base table, which uses its real page count.

## Rewrite rules

| # | Rule | Precondition, as I check it |
|---|---|---|
| 1 | Split `select[p and q]` into stacked selects | none |
| 2 | Push a select below a join or cross | every column it uses comes from one side |
| 3 | Cross plus select becomes join | the predicate is column = column with one column from each side. If the node is already a join, the predicate is ANDed into its condition. |
| 4 | Push a select below a rename, union or union_all, or a project that only passes columns through | names are rewritten through the rename, the project's aliases or the union's positions. I don't push below a rename that sits directly on a scan, because physically that is one aliased seqscan (this keeps the Section 3 example exact). |
| 5 | Push a select below a group_by | it uses grouping columns only |
| 6 | Put a projection below each join and cross | keep only what the ancestors need plus the join's own condition columns. Never below a distinct or a set operator, which need every column. |
| 7 | Drop a projection that keeps every column in order, and merge stacked selects | none |

The rules run in order 1 to 5 (bottom up, sinking each piece as far as it goes), then 6, then 7. `--disable-rule N` turns one off. The Section 3 example reproduces exactly (a test checks the whole tree).

## Cost model

Cost is page reads plus writes, and a plan's cost is the sum over its nodes. I matched the formulas to what my Module 2 executor really does, because the report compares the two:

| Operator | My cost | Where it differs from the handout, and why |
|---|---|---|
| seqscan | M | same |
| filter, project, rename, limit | 0 | same |
| sort | 2M × (1 + merge passes), or 0 if it fits in B − 1 pages | My runs are B − 1 pages, because the input page takes a frame. The handout's "+ M" is reading the input, and the input node already pays for that. |
| nested_loop_join | inner pages × (blocks − 1), blocks = ceil(M / (B − 2)) | Same as M + ceil(M/(B − 2)) × N once the scans pay for M and the first N. An inner that is not a bare table is written to a temp file once, then read once per block, as Module 2 does. |
| sort_merge_join | sort(M) + sort(N) | The handout's + M + N counts the final merge a second time. My two sorts share the frames the way Module 2 splits them. |
| hash_join | 2(M + N) per partitioning level | One level is the handout's 3(M + N) minus the reads the scans already did. I add a level when a partition is still bigger than B − 1 pages, up to depth 2. |
| distinct, group_by | the cheaper of sort and hash | Hash is 0 if the input fits in B − 1 frames, else 2M + 2(B − 1), because Module 2 dumps its full table once before partitioning. |

One convention I kept from the handout even though my executor doesn't do it: a join pays M again when its outer input is another join. That is what makes Section 5.1 come out as 530 and not 510. My executor pipelines that input for free, so my estimates are M too high there, and the report shows it.

## Join enumeration

A join block is the joins, crosses and the selects between them. It is flattened into leaves and conjunct predicates, then rebuilt by dynamic programming over left-deep trees, up to six leaves (more than six are joined in written order). For each subset I try every leaf as the inner, every algorithm that fits the predicates (hash and sort merge need an equality), and keep the cheapest. A subset whose leaves aren't connected by predicates is skipped, unless the whole query has no connected order. Predicates are sorted by text before use, so a query written with commas and the same query written with JOIN ON get the same physical plan (a test checks this). After each join, columns nobody above needs are projected away.

**What left-deep gives up and buys.** It never builds a bushy tree like (A ⋈ B) ⋈ (C ⋈ D), which can be the cheapest when two pairs each shrink a lot. In return the search is much smaller. For each subset there are only |S| ways to choose the inner, not every way to split S in two, so six relations stay fast. Also, every join's right input is a base relation that can be scanned again, and the left side is a stream. So a chain of nested loop joins can pipeline its outer side without writing anything down.

The Section 5.1 table reproduces: every best plan and the 530 total match (a test checks them). One alternative differs. The handout prices "{B′, C} outer, BNL with A inner" as 410 + 40 + 100 = 550, but 40 outer pages at B = 32 need ceil(40 / 30) = 2 blocks, so the formula gives 650. I think the handout's arithmetic is off there, and the winner is the same either way.

## Interface

`opt plan`, `opt explain` and `opt dp-table` take `--logical q.json` (Module 3 output), or `--sql` / `--sql-file`, which compile the SQL first. `opt explain --rewrites` also prints the logical plan before and after rewriting. Every physical node carries `est_rows`, `est_pages` and `est_cost`, where est_cost is for the whole subtree, so the root's est_cost is the predicted total. Module 2 ignores those fields, so the plan runs with no editing. I added three things to Module 2 for these plans: `seqscan` takes an `alias`, there is a physical `rename`, and `nested_loop_join` with no condition is a cross product.
