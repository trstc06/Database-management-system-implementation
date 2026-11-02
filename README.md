# COMP 3005 Project 2: database system

A database system in Python 3.13 using only the standard library. It is built in four modules (storage, execution, SQL front end, optimizer) on top of the relational algebra engine from Bonus Project 1.

## Run it

From this folder:

```sh
python3 -m unittest discover -s tests -t .
./bin/ra --tree "distinct(project[a](R)) union S"
./bin/ra --relations tables.txt --query "project[DID](Employees)"
```

Each tool in `bin/` is a short shell script that runs the matching Python module. Call them by path. `exec` in particular must be run as `./bin/exec`, because a bare `exec` is a shell builtin.

## Layout

| Path | Contents |
|---|---|
| `dbms/common/` | Errors, column types and name resolution shared by every module |
| `dbms/ra/` | The Bonus 1 engine as a library: tokenizer, parser, tree, condition binding, bag operators, JSON serialisation |
| `tests/` | Unit tests, one folder per package |
| `docs/` | Design documents and reports for each module |
| `DESIGN_LOG.md` | What was tried, what broke, and where AI help was wrong |

`docs/REFACTOR.md` explains the week-one changes to the Bonus 1 engine.
