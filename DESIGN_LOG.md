# Design log

## 2026-10-27: Bonus 1 engine as a library

I moved my Bonus 1 code into `dbms/ra/` and split it into separate files that don't print or open files. I added a standalone condition parser, JSON save and load for trees, and bag semantics with a separate `distinct`. Only case 23 changed meaning. See `docs/REFACTOR.md`.

AI mistakes:

- The AI's JSON reader would have reported a bad column as `field 'c' is invalid`, a field that doesn't exist. I saw it while reading the code and fixed the message.
- The AI switched file reading to `Path.open`, which would have broken one of my tests that fakes `open`. I caught it while moving the tests and switched back.

## 2026-11-02: Module 1

I built the page, buffer pool, heap file, catalog, loader and `storage` commands, writing the handout's worked examples as tests first. Sections 3.2 and 5.3 match exactly.

AI mistakes:

- The AI's catalog saved new counts by deleting and reinserting the table's record, which changed the table order after every load. A test caught it (`['S', 'R']` instead of `['R', 'S']`). I now overwrite the record in place.
- The AI's `scan --limit` called `close()` on an `islice`, which crashed. I found it running the commands by hand and fixed it to close the scan itself.

## 2026-11-03: Module 2

I built every operator in Section 2 and the `exec` command. All working memory is pinned frames in the one buffer pool, so a plan can't go over B. My sort makes runs of B − 1 pages because the input page uses a frame. That changes the Section 2.5 example, so I wrote my own numbers in `docs/DESIGN_M2.md`.

AI mistakes:

- The AI's sort told its parent how many pages it was using instead of how many frames it was holding. A sort over an empty table holds 1 frame but said 0, so a sort merge join at B = 4 handed out a frame it didn't have. The empty input test caught it.
- When a hash group by had to split a partition again, the AI's code kept every new partition open while it worked on the first one. At B = 4 that ran out of frames. I now finish all of them before going into any.

## 2026-11-04: Module 3

I wrote the SQL lexer, parser and translator, and the `sql` command. All the Section 5 examples come out as the same plans, and the fifteen required cases are tests. My grammar has small changes from the sketch, listed in `docs/GRAMMAR_SQL.md`. Where duplicates get removed is the first section of `docs/DESIGN_M3.md`.

AI mistakes:

- The AI's JSON catalog reader only understood types written like `VARCHAR(20)`, so a column written as `["name", "VARCHAR", 20]` failed with a schema error. I found it the first time I compiled the handout's examples, and fixed the reader to take both forms.
- The AI put the `Output` class in the translator and imported it into the grouping code, which the translator also imports. That is a circular import. I moved `Output` into `scope.py`, which both files already use.

## 2026-11-04: Module 4

I wrote ANALYZE, the selectivity estimates, the seven rewrite rules, the cost model, the DP join ordering and the `opt` command. The Section 3 rewrite and the Section 5.1 DP table both reproduce. I matched my cost formulas to what Module 2 actually does, and `docs/DESIGN_M4.md` lists every place they differ from the handout. The only files published with the project are the four module pages (a TA confirmed this on the forum), so I built my own dataset (`experiments/m4_data.py`) and twenty-query workload (`experiments/m4_workload.sql`). In Emp, salary is built from age, so the two are strongly correlated.

AI mistakes:

- The AI's hash cost for group_by and distinct left out the dump Module 2 writes when the table overflows. So for one query the optimizer picked hash over sort, and it did 130 I/Os instead of the 88 that sort did. I found it by comparing estimated and actual I/O for every workload query, then added the dump to the formula.
- The AI's new sort cost looped forever when a sort merge join's second sort was left with only 2 frames, because a fan-in of 1 never reduces the number of runs. The tests hung and I had to stop them. Module 2 never merges with fewer than 3 frames, so I made the formula do the same.
- The AI's Module 2 frame rule only worked for one blocking operator at a time. Once the optimizer put a group by over a join at B = 4, the join kept so many frames that the group by was left with 2, and the plan failed. My Module 2 tests never stacked operators, so they missed it. I found it by running every workload plan at B = 4 and 5. Now an operator that can't stream in the frames it's given writes its output to a temp file first.

## 2026-11-05: Integration

I wired the four modules into one shell, `bin/dbms`. It takes SQL plus EXPLAIN, EXPLAIN ANALYZE, ANALYZE table, SET frames = n and LOAD table FROM file. With `--workload`, it reports each query's rows, page I/O, wall time, and estimated against actual root rows. The module tools still work. `ARCHITECTURE.md` describes the whole system.

AI mistakes:

- The AI's statement splitter gave each statement its own line numbers, so in a workload file an error in the third query said "line 2". I saw it in the first workload report and made each statement keep its real line and column.
- In the AI's first version of the shell, error messages showed up before the output of statements that ran earlier. Errors went to stderr right away, while normal output waited in a buffer. I saw it running a setup script and added a flush after each statement's output.
