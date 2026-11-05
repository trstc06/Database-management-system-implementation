# Module 3 design

Code: `dbms/sql/`. Command: `bin/sql`. Tests: `tests/sql/`. Grammar: `docs/GRAMMAR_SQL.md`.

## Where duplicates are removed

SQL works on bags, so I only remove duplicates where the SQL asks for it:

| SQL | Plan | Removes duplicates? |
|---|---|---|
| any SELECT list | project | No, never |
| SELECT DISTINCT | distinct above the project | Yes |
| GROUP BY | group_by | Yes, one row per group |
| UNION ALL | union_all | No |
| UNION | union | Yes |
| INTERSECT, EXCEPT | intersect, except | Yes, set semantics |

The one tricky case is DISTINCT with GROUP BY. If every grouping column is in the select list, the rows are already distinct, so I don't add a distinct node. If one is missing, like `SELECT DISTINCT COUNT(*) FROM Emp GROUP BY did`, two groups can have the same count, so I keep the distinct node. I do it this way because a distinct is never free: it needs a sort or a hash of the whole result, so I only add one when it can change the answer.

## Scope and name resolution

1. **FROM.** Each table reference adds one entry, named by its alias, or by the table name if it has none. Its columns come from the catalog, with that name as their qualifier. A subquery is translated on its own first, and its output columns become the entry under its alias. If two entries have the same name, that's an error. For JOIN ... ON, the ON condition can see the tables to its left plus the new one.
2. **Columns.** `t.c` needs entry `t`, and `t` needs column `c`, and the error says which one failed. A bare `c` must be in exactly one entry. If it's in none, that's an unknown column error. If it's in two or more, that's an ambiguity error that lists them.
3. **SELECT \*** becomes every column of every entry, in FROM order, each one qualified with its entry name.
4. **ORDER BY** first looks for an output name (an AS alias or an aggregate name), then the FROM scope. If the column is not in the select list, the project carries it along, the sort uses it, and a second project drops it. That keeps the canonical order of project, then sort. With DISTINCT this isn't allowed, because the extra column would change which rows are duplicates.

Every column in the plan is fully qualified (`Emp.age`, `e.name`). The only names without a table are ones the query makes itself: aggregate names and `AS` names. A rename above a subquery qualifies those too, so `n` becomes `t.n` outside.

## Aggregates

A query is aggregating if it has GROUP BY or an aggregate in its select list. Its select list, HAVING and ORDER BY can only use grouping columns and aggregates. WHERE and ON can't use aggregates at all.

**Naming rule:** an aggregate is named by its AS alias. Without one, `COUNT(*)` is `count_star` and the rest are `function_table_column`, like `avg_Emp_salary`. These are the same names my Module 2 group_by makes. If the same aggregate is written twice without an alias, it is computed once. So `HAVING COUNT(*) > 5` reuses `COUNT(*) AS n` and becomes `n > 5`, as in example 5.4. An aggregate that only appears in HAVING is still computed by the group_by, and the project drops it.

## Types

Comparisons need the same type on both sides. AVG gives a REAL, which can be compared with an INT. SUM and AVG need an INT column. Set operators need the same number of columns, and the same type in each position.

## Plan fields the handout doesn't name

- `limit` uses `"n"`, since Module 2 says limit returns "the first n tuples".
- An `AS` on a plain column adds `"aliases"` to the project, a list the same length as `"columns"` with `null` where there is no alias. It is left out when nothing is renamed.
- `group_by` aggregates are `[function, column or "*", name]`.
- `CREATE TABLE` compiles to `{"op": "create_table", "table", "columns": [["a", "INT"], ["b", "VARCHAR", 20]]}` and `INSERT` to `{"op": "insert", "table", "values"}`. A table created earlier in the same file can be used by the statements after it.
- `compile --json` prints one plan for one statement and a list for several. `--catalog` works as another name for `--db`, and either one can be a database folder or a JSON catalog file.

I changed Module 2 so it accepts the aggregate names, the project aliases, and REAL compared with INT. That way these plans run once Module 4 makes them physical.

## Reading

- **Nystrom, "Crafting Interpreters".** His parser has one method per grammar rule, and precedence comes from which rule calls which: lower precedence rules call higher ones. I wrote `condition`, `conjunction` and `negation` that way, so NOT, AND and OR precedence lives in the grammar, as Section 2 asks, not in special code. I also took his "panic mode" idea of reporting the first error clearly and stopping.
- **The PostgreSQL documentation on SELECT.** It says UNION, INTERSECT and EXCEPT remove duplicates unless ALL is written, and that ORDER BY can use an output column's name or an input column. That's how I decided where duplicates are removed (the table at the top) and why ORDER BY looks at output names first, then the FROM tables.
