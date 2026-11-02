# Design log

## Week one: engine refactor

### 2026-10-27: Bonus 1 engine as a library

I copied the Bonus 1 code into `dbms/ra/` and split it so the tokenizer, parser, tree and operators import separately and none of them print or open files. The tokenizer's own command line moved into `ra/cli.py`. I added `parse_condition`, JSON round-tripping in the Module 3 logical plan shape, and operators that pull from any source with `scan()`. Projection now keeps duplicates, and `distinct` is a separate operator. Details and the full list of changed tests are in `docs/REFACTOR.md`.

Only numbered case 23 changed meaning, because projection no longer removes duplicates. Three other tests changed for the same reason, and the decimal tests changed because INT is the only numeric type now.

AI mistakes found in this step:

- The AI-written plan deserialiser reused its field reader for each projected column by wrapping the column text in a fake object with the key `c`. A bad column would have produced the message `field 'c' is invalid`, which names a field that does not exist in the plan. I found this while reading the code before running it, and changed the message to name the column, for example `column 'a.' is invalid`. A test now checks the wording.
- When the AI moved the file reading into `ra/cli.py`, it switched to `Path.open`. The ported command-line test replaces `builtins.open` with a mock, which `Path.open` does not use, so the test would have read the real disk and failed with a missing file. I caught this while porting the tests and changed back to the built-in `open`.
