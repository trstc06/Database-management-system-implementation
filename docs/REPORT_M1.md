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

The handout says five traces are published with it, but the only files attached to the project are the four module pages. A TA (Abdelghny Orogat) confirmed this on the course forum on Oct 19. Trace 3 is the only one the handout describes, so I rebuilt it exactly: 80 pages scanned in order, ten times (800 fetches). The other four traces are my own, built in `experiments/m1_measure.py`. The random ones use seed 3005, so every run gives the same traces.

- **own 1, uniform:** 2,000 fetches, each a random page out of 500. The file is bigger than every B.
- **own 2, hot set:** 2,000 fetches over 500 pages. Each fetch has a 90% chance of going to the first 50 pages (10% of the file).
- **own 3, loop:** 20 pages scanned in order, 40 times (800 fetches). The loop is just bigger than B = 16.
- **own 4, scan + hot:** one scan of 500 pages, with a fetch of one of 10 hot pages after each scan page (1,000 fetches).

If the real traces are ever posted, `--traces DIR` runs them instead.

| Trace | B | LRU hit rate | CLOCK hit rate |
|---|---|---|---|
| trace 3 | 4 | 0.000 | 0.000 |
| trace 3 | 16 | 0.000 | 0.000 |
| trace 3 | 64 | 0.000 | 0.000 |
| trace 3 | 256 | 0.900 | 0.900 |
| own 1, uniform | 4 | 0.005 | 0.005 |
| own 1, uniform | 16 | 0.033 | 0.033 |
| own 1, uniform | 64 | 0.122 | 0.122 |
| own 1, uniform | 256 | 0.484 | 0.485 |
| own 2, hot set | 4 | 0.062 | 0.061 |
| own 2, hot set | 16 | 0.250 | 0.244 |
| own 2, hot set | 64 | 0.822 | 0.784 |
| own 2, hot set | 256 | 0.888 | 0.888 |
| own 3, loop | 4 | 0.000 | 0.000 |
| own 3, loop | 16 | 0.000 | 0.000 |
| own 3, loop | 64 | 0.975 | 0.975 |
| own 3, loop | 256 | 0.975 | 0.975 |
| own 4, scan + hot | 4 | 0.099 | 0.090 |
| own 4, scan + hot | 16 | 0.317 | 0.281 |
| own 4, scan + hot | 64 | 0.488 | 0.467 |
| own 4, scan + hot | 256 | 0.490 | 0.485 |

At B = 64, LRU gets a hit rate of 0 on trace 3, with no hits in 800 fetches. The pool only holds the 64 most recently used pages, but the scan uses 79 other pages before it comes back to the same one, so every page is evicted 16 fetches before it is needed again. On each miss LRU evicts the least recently used page, which is always the next one the scan wants, so the misses never stop. CLOCK ends up evicting in the same order. My loop trace shows the same thing one size down: 0 at B = 16, and 0.975 once all 20 pages fit.

I would ship LRU. It ties or beats CLOCK in 19 of the 20 rows, and the one row CLOCK wins is by 2 hits out of 2,000. LRU does better when some pages are used much more than others: 0.822 against 0.784 on the hot set at B = 64, and 0.317 against 0.281 on scan + hot at B = 16. CLOCK only keeps one bit per frame, so it sometimes evicts a hot page that LRU would keep. CLOCK's usual selling point is a cheaper hit, but in my code an LRU hit only moves one entry to the end of an ordered dictionary. LRU is already the default in my code.

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
