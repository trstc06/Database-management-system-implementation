# Module 1 report

All numbers come from `python3 -m experiments.m1_measure --out experiments/results/m1.json`.

## Records per page

Computed is floor(4088 / (L + 4)). Measured is the count on a full page after loading.

| Schema | L | Computed | Measured |
|---|---|---|---|
| (INT, INT, INT) | 12 | 255 | 255 |
| (INT, VARCHAR(10), INT), 10 character values | 20 | 170 | 170 |
| (INT, VARCHAR(200)), 200 character values | 206 | 19 | 19 |

They match because my layout adds nothing per record except the 4 byte slot, which the formula already counts.

## Replacement policies

> **Not finished:** I don't have the published traces yet. This table only has trace 3, rebuilt from the handout (80 pages scanned in order, ten times).

| Trace | B | LRU hit rate | CLOCK hit rate |
|---|---|---|---|
| trace 3 | 4 | 0.00 | 0.00 |
| trace 3 | 16 | 0.00 | 0.00 |
| trace 3 | 64 | 0.00 | 0.00 |
| trace 3 | 256 | 0.90 | 0.90 |

At B = 64, LRU gets no hits on trace 3. Only 64 of the 80 pages fit, and the page LRU throws out is always the one the scan needs next. CLOCK ends up evicting in the same order. I would ship CLOCK for now, because it does as well as LRU here and only sets one bit on a hit.

## Scan cost

A 500 page table scanned from an empty pool:

| B | Reads |
|---|---|
| 2 | 501 |
| 8 | 501 |
| 64 | 501 |

That is 500 data pages plus the directory page. It doesn't drop as B grows, because one scan reads each page only once.

## Sources

Ramakrishnan and Gehrke chapter 9, and O'Neil, O'Neil and Weikum (SIGMOD 1993). See the reading section of `DESIGN_M1.md`.
