# Module 4 report

All numbers come from `python3 -m experiments.m4_measure --out experiments/results/m4.json`.

> **Stand-in data:** the published dataset and twenty-query workload aren't in my folder yet. I used my own: `experiments/m4_data.py` (Emp 4,000 rows on 83 pages, Dept 50, Proj 300, Site 600) and `experiments/m4_workload.sql`. In Emp, salary is 1000 × age plus up to 4,999, so age and salary are strongly correlated. The script takes no other input, so the published files would need a small change to load.

ANALYZE ran on every table first. B = 16 unless I say otherwise.

## Estimation quality

I compared estimated and actual rows at all 146 plan nodes of the twenty queries. For an input read again once per block (a nested loop join's inner), I count the actual rows of one read.

| | q-error |
|---|---|
| Median | 1.0 |
| Worst | 506 (q10, the union) |

The worst nodes, and why they're wrong:

| Query, node | Estimated | Actual | Why |
|---|---|---|---|
| q10 union | 4,050 | 8 | I estimate union as left + right. That is an upper bound that ignores the duplicate removal. There are only 8 cities, so using the product of the ndvs as a cap would have given 8. |
| q13 sort | 417 | 10 | The LIMIT 10 above it stops pulling after 10 rows. The estimate is right for a sort that runs to the end; the node just never got there. |
| q17 sort merge join | 171 | 6 | After a filter I cap each column's ndv at the new row count. Here that lowered ndv(m.eid) from 4,000 to about 160, so the join formula expected far more matches than the managers really give. The cap is fine for the filtered column itself, but wrong for a join key the filter didn't touch. |
| q06, q12 HAVING | 17 | 0 | `n > 100` is on an aggregate, which has no statistics, so it gets the default 1/3. |

Total cost was close. The estimated est_cost and the measured I/O were within 3% for 11 of the 20 queries. The actual is always a little higher, because each scan also reads its directory page. On the tiny queries (q11, q18, q19) that is only 2 I/Os, but 20 to 30% of the total. The biggest real misses were q16 (118 against 146) and q08 (95 against 106). Both are joins with an intermediate outer, where my cost model and my executor disagree (see DESIGN_M4).

## Does optimization pay

Unoptimized means joins in written order, all block nested loop, sort mode for grouping, and no rewrites.

| Query | 1–3, 6, 9, 10, 12–14, 18, 19 | 4 | 5 | 7 | 8 | 11 | 15 | 16 | 17 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|
| Unoptimized / optimized I/O | 1.0 | 1.12 | 1.08 | 3.82 | 8.10 | 84.0 | 1.17 | 3.68 | 3.47 | 1.58 |

The range is 1.0 to 84. The queries at 1.0 read one table or one table per branch, so the scan is the whole cost and there's nothing to reorder. A pushed-down filter costs nothing either way. The big wins are joins. q11 is a comma join written as two cross products, which the optimizer turns into joins in a good order (840 against 10). q08, a four-way chain, drops from 859 to 106 by joining the small Site and Proj tables first.

## Which rule mattered

Total I/O over all twenty queries, with every rule on, then with one rule off:

| Rule | With | Without | Change |
|---|---|---|---|
| 1 split conjunctions | 1,995 | 2,061 | +66 |
| 2 push select below join | 1,995 | 2,100 | +105 |
| 3 cross plus select to join | 1,995 | 2,001 | +6 |
| 4 push below rename, union, project | 1,995 | 1,995 | 0 |
| 5 push below group_by | 1,995 | 1,995 | 0 |
| 6 push projections | 1,995 | 2,422 | +427 |
| 7 tidy | 1,995 | 1,995 | 0 |

Rule 6 is worth the most. Narrower rows mean fewer pages in every sort run, hash partition and nested loop temp file. Rule 2 is next, because a smaller join input means fewer blocks. Rule 1 matters only because rule 2 can't push a conjunction whose parts belong to different sides.

The ones worth nothing here:

- **Rules 4 and 5:** the only subquery predicate in the workload, `t.n > 120`, is on an aggregate, so it can't go below the group_by. And no union has a WHERE.
- **Rule 7:** filters and projects cost 0 I/O in my model and my executor, so removing them is only cosmetic.
- **Rule 3:** worth only 6 here, because my tables are small. A cross product of small inputs stays inside the nested loop block, so it costs comparisons and time, not page I/O. With bigger tables it would matter far more.

## Changing B changes the plan

The chosen joins at B = 4, 16 and 64. For example, q04 uses nested loop at 4 and sort merge at 16 and 64. q16 is the same. q07 goes from (nested loop, sort merge) at 4 to (nested loop, nested loop) at 16 to (sort merge, sort merge) at 64. At small B, sorting costs extra passes. At large B everything fits and the sorts are free.

## Where it breaks

**The query:**

```sql
SELECT * FROM Emp e JOIN Emp m ON e.mgr = m.eid WHERE e.age > 65 AND e.salary > 65000;
```

**The estimate:** sel(age > 65) is about 0.10 and sel(salary > 65000) about 0.16. Multiplying them gives 59 rows.

**The actual:** 383 rows. Anyone over 65 earns at least 66,000, so the second predicate removes almost nothing. The estimate is 6.5 times too low.

**The assumption that failed:** independence. P(age > 65 and salary > 65000) is not P(age > 65) × P(salary > 65000) when salary is built from age.

**What it cost.** At B = 4, the optimizer thinks the filtered outer is 2 pages, which fits in one nested loop block of B − 2 = 2 frames. So it expects to read the inner Emp only once, and it picks nested loop at an estimated 166 I/Os. Really the outer is 11 pages, which is 6 blocks.

| B | Chosen | Estimated | Actual, chosen | Actual, hash join | Actual, sort merge |
|---|---|---|---|---|---|
| 4 | nested loop | 166 | **417** | **354** | 1,070 |
| 5 | nested loop | 166 | 334 | 356 | 734 |
| 6 | nested loop | 166 | 251 | 358 | 712 |
| 16 | nested loop | 166 | 168 | 378 | 344 |

At B = 4 the chosen plan did 417 I/Os where hash join would have done 354, which is 18% more. With the true 383 rows, my model would have priced nested loop at about 581 and picked hash. From B = 5 up, nested loop really is the best anyway, so the wrong estimate only costs something when memory is tight. The cost estimate was off by 2.5 times either way.

**What would have prevented it:** a statistic on the pair of columns, not each one alone. For example, a two-dimensional histogram on (age, salary), or column-group statistics that store the selectivity of the pair. Either one would have told the optimizer that the second predicate keeps almost every row the first one keeps.

## DP table for a four-relation query

q08, `Site s JOIN Proj p JOIN Dept d JOIN Emp e` with a filter on s.city, at B = 16, printed by `opt dp-table`:

```
Subset        Best plan considered                                         Cost
{s}           access s                                                     4
{p}           access p                                                     2
{d}           access d                                                     1
{e}           access e                                                     83
{s, p}        {p} outer, sort_merge_join with s inner                      6
              alternative: {s} outer, sort_merge_join with p inner         6
              alternative: {p} outer, nested_loop_join with s inner        8
              alternative: {s} outer, nested_loop_join with p inner        8
              alternative: {p} outer, hash_join with s inner               10
              alternative: {s} outer, hash_join with p inner               10
{s, d}        no predicate, skipped                                        -
{p, d}        {d} outer, sort_merge_join with p inner                      3
              alternative: {p} outer, sort_merge_join with d inner         3
              alternative: {d} outer, nested_loop_join with p inner        5
              alternative: {p} outer, nested_loop_join with d inner        5
              alternative: {d} outer, hash_join with p inner               7
              alternative: {p} outer, hash_join with d inner               7
{s, e}        no predicate, skipped                                        -
{p, e}        {e} outer, nested_loop_join with p inner                     87
              alternative: {e} outer, sort_merge_join with p inner         111
              alternative: {p} outer, nested_loop_join with e inner        111
              alternative: {p} outer, sort_merge_join with e inner         111
              alternative: {e} outer, hash_join with p inner               113
              alternative: {p} outer, hash_join with e inner               113
{d, e}        no predicate, skipped                                        -
{s, p, d}     {p, d} outer, sort_merge_join with s inner                   8
              alternative: {s, p} outer, sort_merge_join with d inner      8
              alternative: {p, d} outer, nested_loop_join with s inner     10
              alternative: {s, p} outer, nested_loop_join with d inner     10
              alternative: {p, d} outer, hash_join with s inner            12
              alternative: {s, p} outer, hash_join with d inner            12
{s, p, e}     {p, e} outer, sort_merge_join with s inner                   93
              alternative: {p, e} outer, nested_loop_join with s inner     95
              alternative: {p, e} outer, hash_join with s inner            99
              alternative: {s, p} outer, nested_loop_join with e inner     116
              alternative: {s, p} outer, sort_merge_join with e inner      116
              alternative: {s, p} outer, hash_join with e inner            118
{s, d, e}     no predicate, skipped                                        -
{p, d, e}     {p, e} outer, sort_merge_join with d inner                   90
              alternative: {p, e} outer, nested_loop_join with d inner     92
              alternative: {p, e} outer, hash_join with d inner            96
              alternative: {p, d} outer, nested_loop_join with e inner     113
              alternative: {p, d} outer, sort_merge_join with e inner      113
              alternative: {p, d} outer, hash_join with e inner            115
{s, p, d, e}  {s, p, e} outer, sort_merge_join with d inner                95
              alternative: {p, d, e} outer, sort_merge_join with s inner   96
              alternative: {s, p, e} outer, nested_loop_join with d inner  97
              alternative: {p, d, e} outer, nested_loop_join with s inner  98
              alternative: {s, p, e} outer, hash_join with d inner         99
              alternative: {p, d, e} outer, hash_join with s inner         102
              alternative: {s, p, d} outer, nested_loop_join with e inner  118
              alternative: {s, p, d} outer, sort_merge_join with e inner   118
              alternative: {s, p, d} outer, hash_join with e inner         120
```

The written order joins Site, Proj, Dept, then Emp. The DP instead joins Emp to Proj first, because the only predicate on Emp is `e.eid = p.lead`, and Proj is just 2 pages. It adds Site and Dept after that. Pairs with no predicate between them, like {s, d}, are skipped.
