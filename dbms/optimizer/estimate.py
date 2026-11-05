"""Selectivity and cardinality (Section 2).

An Estimate is a node's rows, average width and per column statistics.
Histograms always describe the base table, so a predicate's selectivity
is read from them as a fraction of the base rows, and AND multiplies.
That is the independence assumption.

Defaults when a column has no statistics: = keeps 1/10, a range keeps
1/3, any join predicate that is not column = column keeps 1/10.
"""

import math
from dataclasses import dataclass, field, replace
from ..ra.tree import Node
from .stats import PAGE_SIZE

DEFAULT_EQUALITY = 0.1
DEFAULT_RANGE = 1 / 3
DEFAULT_JOIN = 0.1
WIDE_WIDTH = 8          # count, sum and avg results
FLIP = {'Lt': 'Gt', 'Gt': 'Lt', 'Le': 'Ge', 'Ge': 'Le', 'Eq': 'Eq', 'Ne': 'Ne'}


@dataclass(frozen=True)
class Col:
    ndv: float | None
    width: float
    low: object = None
    high: object = None
    buckets: tuple = ()
    base_rows: int = 0


@dataclass(frozen=True)
class Estimate:
    rows: int
    width: float
    columns: dict = field(default_factory=dict)   # plan column name -> Col
    pages_override: int | None = None              # a base table's real page count

    @property
    def pages(self) -> int:
        if self.pages_override is not None:
            return self.pages_override
        return max(1, math.ceil(self.rows * self.width / PAGE_SIZE))


def clamp_rows(value: float) -> int:
    """Rounded up, never below 1."""
    return max(1, math.ceil(value - 1e-9))


def literal(node: Node):
    return int(node.value) if node.kind == 'Num' else node.value


def fraction_below(col: Col, value, inclusive: bool) -> float:
    """Share of base rows with column < value (or <= when inclusive)."""
    if col.buckets:
        kept = 0.0
        for b in col.buckets:
            if b.high < value or (inclusive and b.high == value):
                kept += b.count
            elif b.low < value or (inclusive and b.low == value):
                kept += b.count * linear(b.low, b.high, value)
        return kept / max(col.base_rows, 1)
    if isinstance(col.low, int) and isinstance(value, int) and col.high is not None:
        if col.high == col.low:
            return 1.0 if value > col.low or (inclusive and value == col.low) else 0.0
        return min(1.0, max(0.0, (value - col.low) / (col.high - col.low)))
    return DEFAULT_RANGE


def linear(low, high, value) -> float:
    if isinstance(low, int) and isinstance(value, int) and high != low:
        return min(1.0, max(0.0, (value - low) / (high - low)))
    return 0.5


def equality(col: Col | None, value) -> float:
    if col is None:
        return DEFAULT_EQUALITY
    if col.buckets:
        for b in col.buckets:
            if b.low <= value <= b.high:
                return b.count / (max(col.base_rows, 1) * b.ndv)
        return 0.0
    if col.ndv:
        return 1 / col.ndv
    return DEFAULT_EQUALITY


def compare_column(col: Col | None, op: str, value) -> float:
    if op == 'Eq':
        return equality(col, value)
    if op == 'Ne':
        return 1 - equality(col, value)
    if col is None or (not col.buckets and col.low is None):
        return DEFAULT_RANGE
    # < and > use the linear fraction alone, as Section 2 says (age > 60 on 20..70 is 10/50).
    # <= and >= add the share equal to the value.
    eq = equality(col, value)
    below = fraction_below(col, value, inclusive=False)
    result = {'Lt': below, 'Le': below + eq, 'Gt': 1 - below, 'Ge': 1 - below + eq}[op]
    return min(1.0, max(0.0, result))


def selectivity(node: Node, columns: dict) -> float:
    kind = node.kind
    if kind == 'And':
        return selectivity(node.children[0], columns) * selectivity(node.children[1], columns)
    if kind == 'Or':
        a, b = (selectivity(c, columns) for c in node.children)
        return a + b - a * b
    if kind == 'Not':
        return 1 - selectivity(node.children[0], columns)
    left, right = node.children
    if left.kind == 'Attr' and right.kind == 'Attr':
        return column_pair(kind, columns.get(left.value), columns.get(right.value))
    if left.kind != 'Attr':
        left, right, kind = right, left, FLIP[kind]
    if left.kind != 'Attr':
        return 0.5   # two literals; rare enough not to evaluate
    return compare_column(columns.get(left.value), kind, literal(right))


def column_pair(kind: str, a: Col | None, b: Col | None) -> float:
    """A = B uses 1 / max(ndv); anything else is the 0.1 default."""
    if kind == 'Eq' and a is not None and b is not None and a.ndv and b.ndv:
        return 1 / max(a.ndv, b.ndv)
    return DEFAULT_JOIN


# Estimates through operators --------------------------------------------

def table_estimate(stats, columns: list[str], alias: str) -> Estimate:
    """A base table scan, its columns named alias.column."""
    cols = {}
    for name in columns:
        s = stats.columns.get(name)
        if s is None:
            cols[f'{alias}.{name}'] = Col(None, stats.width / max(len(columns), 1))
        else:
            cols[f'{alias}.{name}'] = Col(s.ndv, s.width, s.low, s.high, s.buckets, stats.n_tuples)
    return Estimate(clamp_rows(stats.n_tuples), stats.width, cols, pages_override=stats.n_pages)


def cap_ndv(columns: dict, rows: int) -> dict:
    return {name: replace(c, ndv=min(c.ndv, rows) if c.ndv else c.ndv) for name, c in columns.items()}


def filtered(est: Estimate, condition: Node) -> Estimate:
    rows = clamp_rows(est.rows * selectivity(condition, est.columns))
    return Estimate(rows, est.width, cap_ndv(est.columns, rows))


def projected(est: Estimate, names: list[str], aliases: list | None = None) -> Estimate:
    aliases = aliases or [None] * len(names)
    cols = {(alias or name): est.columns.get(name, Col(None, est.width / max(len(names), 1)))
            for name, alias in zip(names, aliases)}
    return Estimate(est.rows, sum(c.width for c in cols.values()), cols)


def renamed(est: Estimate, alias: str) -> Estimate:
    cols = {f"{alias}.{name.rsplit('.', 1)[-1]}": c for name, c in est.columns.items()}
    return replace(est, columns=cols)


def joined(left: Estimate, right: Estimate, condition: Node | None) -> Estimate:
    columns = {**left.columns, **right.columns}
    sel = selectivity(condition, columns) if condition is not None else 1.0
    rows = clamp_rows(left.rows * right.rows * sel)
    if condition is not None:
        columns = equal_ndv(condition, columns)
    return Estimate(rows, left.width + right.width, cap_ndv(columns, rows))


def equal_ndv(condition: Node, columns: dict) -> dict:
    """After A = B both columns have the smaller ndv."""
    if condition.kind == 'And':
        return equal_ndv(condition.children[1], equal_ndv(condition.children[0], columns))
    if condition.kind != 'Eq' or any(c.kind != 'Attr' for c in condition.children):
        return columns
    a, b = (columns.get(c.value) for c in condition.children)
    if a is None or b is None or not a.ndv or not b.ndv:
        return columns
    low = min(a.ndv, b.ndv)
    return {**columns, condition.children[0].value: replace(a, ndv=low),
            condition.children[1].value: replace(b, ndv=low)}


def distinct_rows(est: Estimate, names: list[str]) -> int:
    product = 1.0
    for name in names:
        col = est.columns.get(name)
        if col is None or not col.ndv:
            return est.rows
        product *= col.ndv
    return clamp_rows(min(est.rows, product))


def grouped(est: Estimate, keys: list[str], aggregates: list) -> Estimate:
    rows = distinct_rows(est, keys) if keys else 1
    cols = {key: est.columns[key] for key in keys if key in est.columns}
    for function, argument, name in aggregates:
        width = est.columns[argument].width if function in ('min', 'max') and argument in est.columns else WIDE_WIDTH
        cols[name] = Col(rows, width)
    width = sum(c.width for c in cols.values()) or WIDE_WIDTH
    return Estimate(rows, width, cap_ndv(cols, rows))


def deduplicated(est: Estimate) -> Estimate:
    rows = distinct_rows(est, list(est.columns))
    return Estimate(rows, est.width, cap_ndv(est.columns, rows))


def limited(est: Estimate, n: int) -> Estimate:
    rows = clamp_rows(min(est.rows, n))
    return Estimate(rows, est.width, cap_ndv(est.columns, rows))


def set_operation(op: str, left: Estimate, right: Estimate) -> Estimate:
    """union_all and union add (union is an upper bound), intersect takes the smaller, except the left."""
    rows = {'union_all': left.rows + right.rows, 'union': left.rows + right.rows,
            'intersect': min(left.rows, right.rows), 'except': left.rows}[op]
    return Estimate(rows, left.width, cap_ndv(left.columns, rows))
