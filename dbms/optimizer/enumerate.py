"""Join ordering (Section 5): Selinger style dynamic programming, left-deep.

A join block is a tree of join and cross nodes, plus the selects sitting
between them, over leaves that are anything else (a table access path or
a subquery). The block is flattened into leaves and conjunct predicates,
and the DP rebuilds it: best[S] for every subset S, where the right input
of every join is a single leaf. A subset whose leaves are not connected by
predicates is skipped unless the whole query has no connected order.

A predicate is applied at the first join where all its columns are
present. After each join, columns nobody above needs are projected away
(when rule 6 is on), so later joins see smaller inputs.
"""

from dataclasses import dataclass
from . import estimate as est
from . import logical as lg

MAX_RELATIONS = 6


@dataclass(frozen=True)
class DpRow:
    subset: str
    plan: str
    cost: int | None
    best: bool = True


def popcount(mask: int) -> int:
    return bin(mask).count('1')


class JoinBlock:
    def __init__(self, planner, node: dict) -> None:
        self.planner = planner
        self.node = node
        self.leaves, self.predicates = [], []
        self.collect(node)

    def collect(self, node: dict) -> None:
        op = node['op']
        if op == 'join':
            self.predicates.extend(lg.conjuncts(lg.parse(node['condition'])))
        if op in ('join', 'cross'):
            self.collect(node['left'])
            self.collect(node['right'])
        elif op == 'select' and self.planner.absorbs_selects() and self.planner.over_join(node):
            self.predicates.extend(lg.conjuncts(lg.parse(node['condition'])))
            self.collect(node['child'])
        elif op == 'project' and not node.get('aliases') and self.planner.over_join(node['child']):
            # Rule 6 put this between two joins. The DP narrows after every join itself,
            # so skip it rather than let it cut the block in two.
            self.collect(node['child'])
        else:
            self.leaves.append(node)

    def build(self):
        planner = self.planner
        self.built = [planner.build(leaf) for leaf in self.leaves]
        self.names = [leaf_name(leaf, planner.tables) for leaf in self.leaves]
        owner = {}
        for index, leaf in enumerate(self.leaves):
            for column in lg.outputs(leaf, planner.tables):
                owner[column] = index
        self.pred_masks = [sum(1 << owner[a] for a in set(lg.attributes(p)) if a in owner) for p in self.predicates]
        self.needed = set(lg.outputs(self.node, planner.tables))
        if planner.reorder and len(self.leaves) <= MAX_RELATIONS:
            result = self.dynamic_program()
        else:
            result = self.written_order()
        return self.finish(result)

    # Predicates -----------------------------------------------------------

    def applicable(self, rest: int, inner: int) -> list:
        mask = rest | (1 << inner)
        return [p for p, m in zip(self.predicates, self.pred_masks)
                if m & ~mask == 0 and m & ~rest and m & ~(1 << inner)]

    def remaining(self, mask: int) -> list:
        return [p for p, m in zip(self.predicates, self.pred_masks) if m & ~mask]

    def connected(self) -> bool:
        reached, frontier = 1, [0]
        while frontier:
            current = frontier.pop()
            for m in self.pred_masks:
                if m & (1 << current):
                    for other in range(len(self.leaves)):
                        if m & (1 << other) and not reached & (1 << other):
                            reached |= 1 << other
                            frontier.append(other)
        return reached == (1 << len(self.leaves)) - 1

    def narrowed(self, built, mask: int):
        if 6 not in self.planner.rules:
            return built
        needed = self.needed | set().union(*(lg.attributes(p) for p in self.remaining(mask)))
        columns = list(built.est.columns)
        keep = [c for c in columns if c in needed]
        if keep == columns or not keep:
            return built
        plan = {'op': 'project', 'columns': keep, 'child': built.annotated()}
        return type(built)(plan, est.projected(built.est, keep), built.cost, False)

    # Search -----------------------------------------------------------------

    def label(self, mask: int) -> str:
        return '{' + ', '.join(n for i, n in enumerate(self.names) if mask & (1 << i)) + '}'

    def dynamic_program(self):
        n = len(self.leaves)
        allow_cross = not self.connected()
        best, estimates, rows = {}, {}, []
        for i, built in enumerate(self.built):
            best[1 << i] = built
            rows.append(DpRow(self.label(1 << i), f'access {self.names[i]}', built.cost))
        for size in range(2, n + 1):
            for mask in (m for m in range(1, 1 << n) if popcount(m) == size):
                options = []
                for inner in (i for i in range(n) if mask & (1 << i)):
                    rest = mask & ~(1 << inner)
                    predicates = self.applicable(rest, inner)
                    if rest not in best or (not predicates and not allow_cross):
                        continue
                    for algorithm, built in self.planner.join_candidates(best[rest], self.built[inner], predicates,
                                                                          estimates.get(mask)):
                        estimates.setdefault(mask, built.est)
                        text = f'{self.label(rest)} outer, {algorithm} with {self.names[inner]} inner'
                        options.append((built.cost, len(options), text, self.narrowed(built, mask)))
                if not options:
                    rows.append(DpRow(self.label(mask), 'no predicate, skipped', None))
                    continue
                options.sort(key=lambda o: (o[0], o[1]))
                best[mask] = options[0][3]
                rows.append(DpRow(self.label(mask), options[0][2], options[0][0]))
                rows.extend(DpRow('', f'alternative: {text}', cost, best=False) for cost, _, text, _ in options[1:])
        self.planner.dp_tables.append(rows)
        return best[(1 << n) - 1]

    def written_order(self):
        current, mask = self.built[0], 1
        for inner in range(1, len(self.leaves)):
            predicates = self.applicable(mask, inner)
            options = self.planner.join_candidates(current, self.built[inner], predicates)
            mask |= 1 << inner
            current = self.narrowed(min(options, key=lambda o: o[1].cost)[1], mask)
        return current

    def finish(self, built):
        """Apply any predicate no join used, then restore the written column order."""
        left_over = [p for p, m in zip(self.predicates, self.pred_masks) if popcount(m) < 2]
        if left_over:
            condition = lg.conjoin(left_over)
            plan = {'op': 'filter', 'condition': lg.text(condition), 'child': built.annotated()}
            built = type(built)(plan, est.filtered(built.est, condition), built.cost, False)
        order = lg.outputs(self.node, self.planner.tables)
        if list(built.est.columns) != order:
            plan = {'op': 'project', 'columns': order, 'child': built.annotated()}
            built = type(built)(plan, est.projected(built.est, order), built.cost, False)
        return built


def leaf_name(leaf: dict, tables: dict) -> str:
    if leaf['op'] == 'rename':
        return leaf['to']
    if leaf['op'] == 'scan':
        return leaf['table']
    columns = lg.outputs(leaf, tables)
    return columns[0].rsplit('.', 1)[0] if columns and '.' in columns[0] else 'subquery'
