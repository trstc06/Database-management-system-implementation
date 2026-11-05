"""Parse tree for the SQL subset. Every node remembers where it started."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Pos:
    line: int
    column: int


@dataclass(frozen=True)
class ColumnRef:
    table: str | None
    name: str
    pos: Pos

    def text(self) -> str:
        return f'{self.table}.{self.name}' if self.table else self.name


@dataclass(frozen=True)
class Literal:
    value: int | str
    pos: Pos


@dataclass(frozen=True)
class Aggregate:
    function: str            # count, sum, avg, min, max
    argument: ColumnRef | None   # None means *
    pos: Pos

    def text(self) -> str:
        return f"{self.function.upper()}({'*' if self.argument is None else self.argument.text()})"


@dataclass(frozen=True)
class Comparison:
    op: str                  # = <> < <= > >=
    left: object
    right: object
    pos: Pos


@dataclass(frozen=True)
class Logical:
    op: str                  # and, or, not
    operands: tuple
    pos: Pos


@dataclass(frozen=True)
class SelectItem:
    expr: ColumnRef | Aggregate
    alias: str | None
    pos: Pos


@dataclass(frozen=True)
class Star:
    pos: Pos


@dataclass(frozen=True)
class TableRef:
    name: str
    alias: str | None
    pos: Pos


@dataclass(frozen=True)
class SubqueryRef:
    query: object
    alias: str
    pos: Pos


@dataclass(frozen=True)
class FromClause:
    refs: tuple              # TableRef | SubqueryRef
    conditions: tuple        # ON condition for refs[1:] in a JOIN chain, empty for commas
    uses_join: bool


@dataclass(frozen=True)
class OrderItem:
    column: ColumnRef
    descending: bool


@dataclass(frozen=True)
class SelectBlock:
    distinct: bool
    items: tuple | Star
    source: FromClause
    where: object | None
    group_by: tuple          # ColumnRef
    having: object | None
    order_by: tuple          # OrderItem
    limit: int | None
    pos: Pos


@dataclass(frozen=True)
class SetOperation:
    op: str                  # union, union_all, intersect, except
    left: object
    right: object
    pos: Pos


@dataclass(frozen=True)
class ColumnDef:
    name: str
    type: str                # INT or VARCHAR
    length: int | None
    pos: Pos


@dataclass(frozen=True)
class CreateTable:
    table: str
    columns: tuple
    pos: Pos


@dataclass(frozen=True)
class Insert:
    table: str
    values: tuple            # Literal
    pos: Pos
