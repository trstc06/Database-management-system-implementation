# Module 1 design

Code: `dbms/storage/`. Command: `bin/storage`. Tests: `tests/storage/`.

## Records and pages

A record is its values in column order, with no header. INT is 4 bytes. VARCHAR(n) is a 2 byte length, then the UTF-8 bytes. All numbers are little-endian.

This is page 1 of `R.db` for `R(a INT, b VARCHAR(5), c INT)` after inserting (42,'bob',7), (43,'amy',9), (44,'al',1) and deleting the second row:

```
byte   bytes                                     meaning
   0   01 00 00 00                               page_id = 1
   4   03 00                                     slot_count = 3 (includes the dead slot)
   6   da 0f                                     free_end = 4058
   8   f3 0f 0d 00                               slot 0: offset 4083, length 13
  12   00 00 00 00                               slot 1: dead
  16   da 0f 0c 00                               slot 2: offset 4058, length 12
  20   00 ...                                    free space, 4038 bytes
4058   2c 00 00 00 02 00 61 6c 01 00 00 00       44, 'al', 1
4070   2b 00 00 00 03 00 61 6d 79 09 00 00 00    old bytes of 43, 'amy', 9
4083   2a 00 00 00 03 00 62 6f 62 07 00 00 00    42, 'bob', 7
```

Slots are never reused, so a rid always points to the same record. The largest record I can store is 4084 bytes, not 4088, because a record also needs its 4 byte slot.

## Free space and compaction

Insert uses first fit: it takes the first data page in the directory with at least L + 4 free bytes, or adds a new data page if none has room. Delete only marks the slot dead. I don't compact pages, so deleted bytes are never reused and a page's free space never changes on delete.

A scan copies a directory page's list of data pages, unpins it, then reads one data page at a time. So it only pins one page, which is what Module 2 needs for a table scan.

## Buffer pool

The pool has B frames and looks pages up by (file, page id). It counts hits, misses, evictions, reads and writes exactly as Section 5 says. A pinned page is never pushed out. LRU picks the least recently used unpinned frame. CLOCK follows the pseudocode in Section 5.2. Both reproduce Section 5.3.

## Catalog format

`catalog.db` is a heap file. Each record starts with an INT saying what kind it is:

| Kind | Record | Fields |
|---|---|---|
| 1 | TABLE | name, file, num_pages, num_tuples |
| 2 | COLUMN | table, position, name, type, length (0 for INT) |
| 3, 4 | saved for Module 4 | statistics and histogram buckets |

Names and types are VARCHAR(255); the rest are INT. `num_pages` counts data pages only. After a load I overwrite the TABLE record in place, since its length doesn't change. The catalog has its own 4 frame buffer pool, so `storage scan` only counts table pages.

## Loader

The CSV reader is hand-written. Values with commas go in double quotes, and `""` inside quotes is one quote. Blank lines are skipped. The whole file is checked before anything is inserted, and errors give the line and column.

## Reading

- **Ramakrishnan and Gehrke, chapter 9.** The slotted page and heap file directory come from here. Its point that a rid must keep pointing to the same record is why I never renumber slots and why I overwrite catalog records in place.
- **O'Neil, O'Neil and Weikum, "The LRU-K Page Replacement Algorithm", SIGMOD 1993.** LRU only looks at when a page was last used, so a scan can push out pages that are used often. This explains my trace 3 results.
