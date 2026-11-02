# Week one: Bonus 1 engine as a library

The Bonus Project 1 engine now lives in `dbms/ra/` as a library the other modules import. The original submission in `RA_Engine (BONUS)/` is unchanged.

## The seven points

| Point | Where it is done |
|---|---|
| 1. Separate layers | `tokenizer.py`, `parser.py`, `tree.py` and `operators.py` import independently. None of the library modules prints, opens a file or reads input; only `cli.py` does. `tests/ra/test_operators.py` checks both by reading the source. |
| 2. Condition parser on its own | `parser.parse_condition(text)` parses a string such as `R.a > 500 and R.b = 'cs'`. `conditions.bind_condition(tree, columns)` resolves and type-checks it once and returns a row predicate. |
| 3. Qualified names | Every `Column` (in `dbms/common/schema.py`) has a qualifier and a name, so `qualified_name` (`t.c`) always exists. Duplicate checks use the qualified name. Bare names still resolve when exactly one column matches. Only printing keeps the Bonus 1 rule of showing bare names until a product. |
| 4. Serialise the tree | `serialize.to_json` / `from_json` use the Module 3 logical plan shape (`op`, `child`, `left`, `right`, conditions as strings). `condition_to_text` prints a condition with only the parentheses its structure needs. A round-trip test covers every node type and every condition shape. |
| 5. Operators detached from data | An operator takes any source with `columns` and `scan()` and returns a lazy `Stream`. Nothing is read when the operator is built; names and types are still checked then. |
| 6. Bags, not sets | `project` keeps duplicates. `distinct` is a separate operator, written `distinct(expr)` in the algebra. Relation files load as bags. `union_all` keeps duplicates; `union`, `intersect` and `except` return sets. |
| 7. Test suite kept | All Bonus 1 tests were ported to `tests/ra/`. The changes are listed below. |

## Language changes

- `<>` is accepted as well as `!=`, and both mean not equal. Printed conditions use `<>`.
- `and`, `or` and `not` in conditions ignore case, so `AND`, `Or` and `NOT` read the same. Query operators (`union`, `select`, ...) keep their lowercase spelling.
- `distinct(expr)` removes duplicates. A name `distinct` not followed by `(` is still a relation name.
- Numbers are integers only, because INT is the only numeric type in the project. `-30.5` is a lexical error in a query and in a relation file.
- Column types are `INT` and `VARCHAR` instead of number and string. Type errors still say both, for example `cannot compare INT (number) with VARCHAR (string)`.

## Tests that changed

Of the twenty five numbered cases, only case 23 changed meaning, and it changed under point 6.

| Test | Bonus 1 | Now | Reason |
|---|---|---|---|
| Case 23 | `project[DID](Employees)` returned D1, D2 | Returns D1, D2, D1; `distinct(...)` returns D1, D2 | Point 6, projection keeps duplicates |
| `test_projection_order_and_qualifiers` | 4 rows from a projected product | 6 rows; `distinct` gives 4 | Point 6 |
| `test_cli_result_and_empty_body` | `'D1'`, `'D2'` | `'D1'`, `'D2'`, `'D1'` | Point 6 |
| `test_measurements_reset_and_do_not_change_results` | `project[b](R)` gave 1 row | 3 rows | Point 6 |
| `test_integer_and_string_values_in_a_bag` (was `..._own_equality`) | Decimals, duplicate rows merged | Integers, duplicate rows kept, decimals rejected | INT only; point 6 |
| `test_whole_names_and_integers_only`, `test_whole_fields_and_quoted_values` | `-30.5` was a number | Lexical error | INT only |
| `test_empty_definition_types_and_empty_operators` | type `number` | type `INT` | INT and VARCHAR types |
| Tokenizer and parser command tests | `tokenizer.py QUERY` | `ra --tokens QUERY` | The tokenizer no longer has its own `main`, so the library does not print |

New tests: `test_conditions.py` (standalone parser, case-insensitive keywords, type rules, UTF-8 order), `test_serialize.py` (round trips and malformed-plan messages) and `test_operators.py` (laziness, rescans, bag and set results, layer checks).
