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
