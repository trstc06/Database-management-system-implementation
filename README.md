# A small database system (COMP 3005, Project 2)

**Demo video:** https://youtu.be/DzHiHRwjZ8s

This is a working database system that I wrote from scratch in Python. You type SQL, and it gives back rows, the same way PostgreSQL does, but much smaller. Nothing is borrowed: the parser, the optimizer, the query engine and the on-disk storage are all my own code, using only Python's standard library.

It is built in four parts, called modules, plus a shell that joins them together:

| Module | What it does | Its tool |
|---|---|---|
| M1 Storage | Keeps tables on disk in 4 KB pages and reads them through a buffer pool (a small, fixed set of pages kept in memory) | `bin/storage` |
| M2 Execution | Runs a query plan: scans, filters, sorts, joins, grouping, all inside a memory limit | `bin/exec` |
| M3 SQL front end | Reads SQL text, checks every name and type, and turns it into a logical plan (what to compute) | `bin/sql` |
| M4 Optimizer | Turns the logical plan into a physical plan (how to compute it), choosing the join order and algorithms from statistics | `bin/opt` |
| Shell | One prompt that sends SQL through M3, M4, M2 and M1 | `bin/dbms` |

It also includes my relational algebra engine from Bonus Project 1, which the other modules reuse (`bin/ra`).

## What you need

- Python 3.9 or newer. I tested it on 3.9 and 3.13. Check with `python3 --version`.
- Nothing else. There is nothing to install.

All commands below are run from this folder, the one that holds this README.

## Try it in five minutes

**1. Run the tests.** You should see `OK` at the end.

```sh
python3 -m unittest discover -s tests -t .
```

**2. Build the sample database.** It has four tables: Emp (4,000 employees), Dept (50 departments), Proj (300 projects) and Site (600 sites).

```sh
python3 -m experiments.m4_data --db demo
```

**3. Open the shell.**

```sh
./bin/dbms --db demo
```

You get a `dbms>` prompt. Every statement ends with a semicolon. Type `quit` to leave.

**4. Ask it something.**

```sql
SELECT COUNT(*) FROM Emp;
SELECT d.city, COUNT(*) AS people FROM Emp e JOIN Dept d ON e.did = d.did GROUP BY d.city ORDER BY people DESC LIMIT 3;
```

**5. See how it answered.** First collect statistics, then ask for the plan.

```sql
ANALYZE Emp;
ANALYZE Dept;
EXPLAIN ANALYZE SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did WHERE e.age > 60;
```

The plan is printed as a tree. Read it from the bottom up, because data flows upward: the scans read the tables, the filter keeps the rows that match, and the join puts the two sides together. Each line shows what the optimizer guessed (`est rows`, `est cost`) next to what really happened (`actual`, `reads`, `writes`). A `-` means that step read or wrote no pages itself.

## What the shell understands

| You type | It does |
|---|---|
| `CREATE TABLE T (a INT, b VARCHAR(20));` | Makes a table. The types are `INT` and `VARCHAR(n)` with n up to 255. |
| `INSERT INTO T VALUES (1, 'x');` | Adds one row. |
| `LOAD T FROM 'file.csv';` | Adds every row of a CSV file (no header line, commas between fields). |
| `SELECT ...;` | Runs a query. See the list below. |
| `ANALYZE T;` | Collects statistics on T, which the optimizer uses to make better choices. |
| `EXPLAIN SELECT ...;` | Shows the plan with its estimates, without running it. |
| `EXPLAIN ANALYZE SELECT ...;` | Runs the query and shows estimates next to the real numbers. |
| `SET frames = 8;` | Changes the memory limit B to 8 pages. The default is 32, the smallest is 4. |

The SQL it supports is the subset from the Module 3 handout: `SELECT` with `DISTINCT`, joins written with commas or `JOIN ... ON`, `WHERE`, `GROUP BY`, `HAVING`, `ORDER BY`, `LIMIT`, the aggregates `COUNT`, `SUM`, `AVG`, `MIN` and `MAX`, a subquery in `FROM` with a name, and `UNION`, `UNION ALL`, `INTERSECT` and `EXCEPT`. It does not support `UPDATE`, `DELETE`, `NULL`, indexes or arithmetic like `salary * 2`. `docs/GRAMMAR_SQL.md` has the full grammar.

The shell can also run a file of statements, or a workload (a file of queries) with a report of how each one went:

```sh
./bin/dbms --db demo --file setup.sql
./bin/dbms --db demo --workload queries.sql --out report.md
```

The workload report gives, for each query, the number of rows, the pages read and written, the time it took, and the estimated against the actual number of rows.

## Each module on its own

Each module has its own command line tool, so it can be tested without the others. The tools that read data (`storage`, `exec`, `opt`) take `--frames B`, the memory limit in pages, and `--json` gives machine-readable output with all the counters. Run `./bin/<tool> --help` to see every option.

```sh
# M1: storage
./bin/storage create-table --db mydb --name R --schema "a:INT,b:VARCHAR(20),c:INT"
./bin/storage load   --db mydb --table R --file r.csv
./bin/storage scan   --db mydb --table R --frames 8 --json
./bin/storage layout --db mydb --table R --page 1          # the bytes of one page, slot by slot
./bin/storage trace  --file trace.txt --frames 16 --policy lru --json   # buffer pool hits and misses

# M2: execution (a physical plan written as JSON)
./bin/exec run   --db demo --plan plan.json --frames 32 --json
./bin/exec trace --db demo --plan plan.json --frames 32    # counters for each operator

# M3: SQL to a logical plan
./bin/sql parse   --query "SELECT name FROM Emp" --explain  # the parse tree, no database needed
./bin/sql check   --db demo --file q.sql                    # errors only
./bin/sql compile --db demo --file q.sql --json > q.json    # the logical plan

# M4: optimizer
./bin/opt analyze  --db demo --table Emp
./bin/opt explain  --db demo --logical q.json --frames 32
./bin/opt plan     --db demo --logical q.json --frames 32 --json > plan.json
./bin/opt dp-table --db demo --logical q.json --frames 32   # the join order search, one row per set of tables
```

The output of each step is the input of the next, so `sql compile` then `opt plan` then `exec run` does the same work as the shell, one module at a time.

Always run the tools with their path, like `./bin/exec`. Typing just `exec` runs your terminal's own `exec` command, which is something different.

**On Windows**, or if `./bin/...` says "permission denied", run the same module with Python instead. These do exactly the same thing:

| Script | Same as |
|---|---|
| `./bin/dbms` | `python3 -m dbms.shell.cli` |
| `./bin/storage` | `python3 -m dbms.storage.cli` |
| `./bin/exec` | `python3 -m dbms.execution.cli` |
| `./bin/sql` | `python3 -m dbms.sql.cli` |
| `./bin/opt` | `python3 -m dbms.optimizer.cli` |
| `./bin/ra` | `python3 -m dbms.ra.cli` |

## Where the report numbers come from

Every number in my reports comes from a script in `experiments/`. Running one rebuilds its data and writes the results to `experiments/results/`. The results are the same every time, apart from the timing fields.

```sh
python3 -m experiments.m1_measure --out experiments/results/m1.json
python3 -m experiments.m2_measure --out experiments/results/m2.json --plot docs/m2_crossover.svg
python3 -m experiments.m4_measure --out experiments/results/m4.json
```

The course said the sample traces, datasets and workloads would be published, but they never were, and a TA said on the forum to use our own and document them. So the data in these scripts is my own. Each report says what it used.

## What is in this folder

| Path | Contents |
|---|---|
| `bin/` | The six command line tools. Each is a short script that runs a Python module. |
| `dbms/storage/` | M1: pages, the buffer pool, heap files, the catalog and the CSV loader |
| `dbms/execution/` | M2: the operators, external sort, the three joins and the plan runner |
| `dbms/sql/` | M3: the SQL lexer, parser, name resolution and translation to logical plans |
| `dbms/optimizer/` | M4: ANALYZE and statistics, estimates, rewrite rules, the cost model and join ordering |
| `dbms/shell/` | The shell that joins the four modules, and the workload report |
| `dbms/ra/` | My Bonus 1 relational algebra engine, split up so the other modules can use it |
| `dbms/common/` | Errors and column types shared by every module |
| `experiments/` | The scripts behind every number in my reports, and the sample data |
| `tests/` | The tests, one folder per module |
| `docs/` | The design documents and reports |

To read about the design:

- `ARCHITECTURE.md`: how the whole system fits together, what passes between the modules, and what I would do differently.
- `DESIGN_LOG.md`: what I tried, what broke, and where AI help was wrong.
- `docs/REFACTOR.md`: how I turned the Bonus 1 engine into a library.
- `docs/DESIGN_M1.md` and `docs/REPORT_M1.md`: storage.
- `docs/DESIGN_M2.md` and `docs/REPORT_M2.md`: execution.
- `docs/GRAMMAR_SQL.md` and `docs/DESIGN_M3.md`: the SQL front end.
- `docs/DESIGN_M4.md` and `docs/REPORT_M4.md`: the optimizer.

When a document says something like "Section 2.5", it means that section of the course handout for the same module. The words below are explained in the last table of this file.

## Words used in this project

| Word | Meaning |
|---|---|
| page | A fixed 4096-byte block. Tables are stored on disk as pages, and all I/O is counted in pages. |
| slotted page | My page layout. A small directory at the front (the slots) says where each record sits, and the records fill in from the back. |
| rid | Record id: (page number, slot number). It points to one row and never changes. |
| heap file | One table's file: its pages in no particular order, plus directory pages that track free space. |
| buffer pool | The pages kept in memory. Reading a page that is already there is a hit, and having to read it from disk is a miss. |
| frame | One page-sized slot in the buffer pool. |
| B | The memory limit, in frames. No query may hold more than B pages at once. |
| pin | Marking a page as in use so the pool cannot throw it out. |
| LRU, CLOCK | Two rules for choosing which page to throw out when the pool is full. |
| catalog | The table that describes the other tables: their columns, sizes and statistics. |
| bag | A collection that can hold duplicates. SQL works on bags, so a query keeps duplicate rows unless you write DISTINCT. |
| logical plan | What a query computes, as a tree of steps like join or select, with no algorithm chosen yet. |
| physical plan | How to compute it: the same tree with an algorithm for every step, like `hash_join`. |
| operator | One step of a plan, like a scan, a filter or a join. Each one hands rows to the step above it. |
| external sort | Sorting data bigger than memory by sorting pieces (runs), writing them to disk and merging them. |
| nested loop, sort merge, hash join | The three join algorithms. The optimizer picks the cheapest for each join. |
| statistics | Facts about a table that ANALYZE collects: row count, page count, number of distinct values, and a histogram for each column. |
| histogram | How a column's values are spread out, in buckets that each hold about the same number of rows. |
| ndv | Number of distinct values in a column. |
| selectivity | The fraction of rows a condition keeps, from 0 to 1. |
| cost | The estimated number of pages a plan reads and writes. |
| q-error | How far off an estimate was: the larger of estimate and actual, divided by the smaller. 1 means exact. |
