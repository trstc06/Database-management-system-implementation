# SQL grammar

Code: `dbms/sql/lexer.py` and `dbms/sql/parser.py`. Each rule below is one parser method.

## EBNF

```
statements   ::= statement { ";" statement } [ ";" ]
statement    ::= create | insert | query

create       ::= CREATE TABLE ident "(" coldef { "," coldef } ")"
coldef       ::= ident ( INT | VARCHAR "(" int ")" )
insert       ::= INSERT INTO ident VALUES "(" literal { "," literal } ")"
literal      ::= int | string

query        ::= select_block { setop select_block }
setop        ::= UNION [ ALL ] | INTERSECT | EXCEPT
select_block ::= SELECT [ DISTINCT ] select_list
                 FROM from_list
                 [ WHERE condition ]
                 [ GROUP BY column { "," column } [ HAVING condition ] ]
                 [ ORDER BY sort_item { "," sort_item } ]
                 [ LIMIT int ]
               | "(" query ")"

select_list  ::= "*" | select_item { "," select_item }
select_item  ::= ( column | aggregate ) [ AS ident ]
aggregate    ::= ( COUNT | SUM | AVG | MIN | MAX ) "(" ( column | "*" ) ")"
sort_item    ::= column [ ASC | DESC ]

from_list    ::= table_ref { "," table_ref }
               | table_ref JOIN table_ref ON condition { JOIN table_ref ON condition }
table_ref    ::= ident [ [ AS ] ident ]
               | "(" query ")" [ AS ] ident

condition    ::= conjunction { OR conjunction }
conjunction  ::= negation { AND negation }
negation     ::= NOT negation | "(" condition ")" | comparison
comparison   ::= operand ( "=" | "<>" | "<" | "<=" | ">" | ">=" ) operand
operand      ::= column | aggregate | int | string
column       ::= ident [ "." ident ]
```

Tokens: keywords are case insensitive, identifiers are case sensitive, `int` is digits with an optional `+` or `-`, strings are single quoted with `''` for a quote, and `--` comments run to the end of the line.

## Precedence and associativity

| Level | Operators | Grouping | Rule that does it |
|---|---|---|---|
| tightest | NOT | right (it nests) | `negation` calls itself |
| | AND | left | `conjunction` loops |
| | OR | left | `condition` loops |
| | UNION, UNION ALL, INTERSECT, EXCEPT | left, all one level | `query` loops over blocks |

So `NOT a = 1 AND b = 2 OR c = 3` is `((NOT a = 1) AND b = 2) OR c = 3`, and `A UNION B EXCEPT C` is `(A UNION B) EXCEPT C`. Parentheses are the only way to change this.

## Where I differ from the Section 2 sketch

- **`AS` is optional before a table or subquery alias.** The handout's own examples write `FROM Emp e`. I kept `AS` required in the select list, so a missing comma like `SELECT a b` is an error, not a silent alias.
- **An operand can be an aggregate.** Otherwise `HAVING COUNT(*) > 5` would not parse. An aggregate in WHERE or ON parses fine and is then rejected as an aggregate error, which is what required case 7 asks for.
- **I wrote out `sort_item`.** The sketch uses `sort_list` without defining it.
- **`(*)` parses for every aggregate** but only `COUNT(*)` passes the checks after parsing.
- **A FROM list is commas or JOINs, never both.** That is the sketch's rule, and mixing them is a syntax error.
- **ORDER BY and LIMIT belong to one select block**, as in the sketch. `A UNION B ORDER BY x` sorts B. To sort a whole union, put it in a subquery.
