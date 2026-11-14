"""How the shell shows results and plans to a person.

Only the shell uses this. The module tools (exec, opt, storage) keep the
exact text formats their handouts describe.

Results are a lined-up table: text on the left, numbers on the right.
Plans are a drawn tree with one header row, read from the bottom up.
"""

from ..optimizer.explain import node_label

CHILD_KEYS = ('child', 'left', 'right')   # same order Module 2 lists its plan nodes in
MIDDLE, LAST = '├─ ', '└─ '
PIPE, SPACE = '│  ', '   '
NOTHING = '-'                             # shown instead of a zero page count
MAX_LISTED_COLUMNS = 5                   # a longer project list is cut short
SHOWN_COLUMNS = 3


def show_value(value) -> str:
    return f'{value:.4f}' if isinstance(value, float) else str(value)


def is_number(value) -> bool:
    return isinstance(value, (int, float))


def row_count(count: int) -> str:
    return '(1 row)' if count == 1 else f'({count} rows)'


def format_table(header: list[str], rows: list[tuple]) -> str:
    """A table like psql's: ' a | b', a rule, then one line per row."""
    cells = [[show_value(v) for v in row] for row in rows]
    widths = [max([len(name)] + [len(line[i]) for line in cells]) for i, name in enumerate(header)]
    numeric = [bool(rows) and all(is_number(row[i]) for row in rows) for i in range(len(header))]

    def line(values: list[str], right: list[bool]) -> str:
        padded = [v.rjust(w) if r else v.ljust(w) for v, w, r in zip(values, widths, right)]
        return (' ' + ' | '.join(padded)).rstrip()

    rule = '+'.join('-' * (width + 2) for width in widths)
    lines = [line(header, [False] * len(header)), rule] + [line(values, numeric) for values in cells]
    lines.append(row_count(len(rows)))
    return '\n'.join(lines)


def short_label(plan: dict) -> str:
    """A project of many columns (SELECT * over a join) would push every number off screen."""
    columns = plan.get('columns', [])
    if plan['op'] != 'project' or len(columns) <= MAX_LISTED_COLUMNS or plan.get('aliases'):
        return node_label(plan)
    return f"project [{', '.join(columns[:SHOWN_COLUMNS])}, ... {len(columns)} columns]"


def tree_labels(plan: dict, lead: str = '', branch: str = '') -> list[str]:
    """One label per node in plan order, with branches drawn in front of it."""
    labels = [lead + branch + short_label(plan)]
    children = [plan[key] for key in CHILD_KEYS if key in plan]
    below = lead + (PIPE if branch == MIDDLE else SPACE if branch == LAST else '')
    for position, child in enumerate(children):
        labels += tree_labels(child, below, LAST if position == len(children) - 1 else MIDDLE)
    return labels


def plan_nodes(plan: dict) -> list[dict]:
    nodes = [plan]
    for key in CHILD_KEYS:
        if key in plan:
            nodes += plan_nodes(plan[key])
    return nodes


def format_grid(header: list[str], labels: list[str], columns: list[list[str]]) -> list[str]:
    """The plan tree on the left, then right-aligned number columns."""
    label_width = max(len(label) for label in labels + [header[0]]) + 2
    widths = [max(len(name), *(len(v) for v in values)) for name, values in zip(header[1:], columns)]
    lines = [(header[0].ljust(label_width) + '  '.join(n.rjust(w) for n, w in zip(header[1:], widths))).rstrip()]
    for index, label in enumerate(labels):
        values = '  '.join(column[index].rjust(w) for column, w in zip(columns, widths))
        lines.append(label.ljust(label_width) + values)
    return lines


def format_explain(plan: dict) -> str:
    nodes = plan_nodes(plan)
    columns = [[str(n['est_rows']) for n in nodes], [str(n['est_cost']) for n in nodes]]
    return '\n'.join(format_grid(['plan', 'est rows', 'est cost'], tree_labels(plan), columns))


def actual_rows(counts: dict) -> str:
    """Rows per run. A nested loop's inner side runs once per block, so say how often."""
    loops = max(counts['opens'], 1)
    rows = str(round(counts['tuples_out'] / loops))
    return rows if loops == 1 else f'{rows} (x{loops})'


def page_count(value: int) -> str:
    return str(value) if value else NOTHING


def format_explain_analyze(plan: dict, counters: list[dict], seconds: float, page_io: int,
                           rows: int, frames: int) -> str:
    """The plan with estimated next to actual at every node, then a short summary."""
    nodes = plan_nodes(plan)
    columns = [[str(n['est_rows']) for n in nodes], [actual_rows(c) for c in counters],
               [str(n['est_cost']) for n in nodes], [page_count(c['page_reads']) for c in counters],
               [page_count(c['page_writes']) for c in counters]]
    header = ['plan', 'est rows', 'actual', 'est cost', 'reads', 'writes']
    lines = format_grid(header, tree_labels(plan), columns)
    lines += ['',
              f'result:   {row_count(rows)[1:-1]} in {seconds * 1000:.1f} ms',
              f"page I/O: {page_io} actual, {plan['est_cost']} estimated",
              f'memory:   {frames} frames']
    return '\n'.join(lines)
