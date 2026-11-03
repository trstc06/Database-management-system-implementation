# COMP 3005 Project 2: database system

This is a small database system written in Python 3.13 with only the standard library. I am building it in four modules: storage, execution, a SQL front end and an optimizer. It sits on top of my relational algebra engine from Bonus Project 1.

## Run it

From this folder:

```sh
python3 -m unittest discover -s tests -t .
./bin/ra --tree "distinct(project[a](R)) union S"
./bin/ra --relations tables.txt --query "project[DID](Employees)"
./bin/storage create-table --db db --name R --schema "a:INT,b:VARCHAR(20),c:INT"
./bin/storage load --db db --table R --file r.csv
./bin/storage scan --db db --table R --frames 8 --json
./bin/storage layout --db db --table R --page 1
./bin/storage trace --file trace.txt --frames 16 --policy clock --json
python3 -m experiments.m1_measure --out experiments/results/m1.json
```

Each tool in `bin/` is a short script that runs the matching Python module. Run them with their path, like `./bin/storage`. This matters most for `exec`, because typing just `exec` runs the shell's own `exec` command instead of mine.

## What the folders hold

| Path | Contents |
|---|---|
| `dbms/common/` | Errors, column types and column name lookup, shared by every module |
| `dbms/ra/` | My Bonus 1 engine, split up so other modules can use it |
| `dbms/storage/` | Module 1: pages, the buffer pool, heap files, the catalog and the CSV loader |
| `experiments/` | Scripts that produce every number in my reports |
| `tests/` | Tests, one folder per module |
| `docs/` | Design documents and reports |
| `DESIGN_LOG.md` | What I tried, what broke, and where AI help was wrong |

`docs/REFACTOR.md` explains how I changed the Bonus 1 engine in week one. `docs/DESIGN_M1.md` and `docs/REPORT_M1.md` cover Module 1.
