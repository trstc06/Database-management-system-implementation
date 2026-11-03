# Week one refactor

| Change | What I did |
|---|---|
| 1. Separate layers | Tokenizer, parser, tree and operators are separate files. Only `cli.py` prints or opens files. |
| 2. Condition parser | `parse_condition("R.a > 500 and R.b = 'cs'")` works on its own. |
| 3. Qualified names | Every column knows its table, so `R.a` always works. |
| 4. JSON | `to_json` and `from_json` save and load every kind of node. |
| 5. Data source | Operators read from anything with a `scan()` method. |
| 6. Bags | Projection keeps duplicates. `distinct(...)` removes them. |
| 7. Tests | All 89 Bonus 1 tests moved to `tests/ra/`. |

I also added `<>`, made `and`/`or`/`not` work in any case, and only allow whole numbers since INT is the only number type.

## Tests that changed

Only case 23 changed meaning: `project[DID](Employees)` now returns D1, D2, D1 instead of D1, D2. Three other tests changed for the same reason. The decimal tests changed because decimals are no longer allowed, and the type name changed from `number` to `INT`.
