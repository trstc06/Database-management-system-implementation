"""Logical plan in, physical plan out (Sections 4 to 6).

Every physical node carries est_rows, est_pages and est_cost, where
est_cost is the estimated I/O of the whole subtree under it, so the root's
est_cost is the plan's predicted total. Module 2 ignores these fields.
"""

from dataclasses import dataclass
from ..common.errors import DbError
from . import cost as costs
from . import estimate as est
from . import logical as lg
from .rewrite import ALL_RULES, rewrite
from .stats import TableStats

ALGORITHMS = ('nested_loop_join', 'hash_join', 'sort_merge_join')
MIN_FRAMES = 4


@dataclass(frozen=True)
class Built:
    plan: dict
    est: est.Estimate
    cost: int
    is_base: bool = True        # no join inside: a table access path or a subquery's top

    def annotated(self) -> dict:
        return {**self.plan, 'est_rows': self.est.rows, 'est_pages': self.est.pages, 'est_cost': self.cost}


class Planner:
    def __init__(self, tables: dict, stats: dict, frames: int, rules=ALL_RULES, *,
                 reorder: bool = True, join_algorithm: str | None = None, aggregate_mode: str | None = None) -> None:
        if frames < MIN_FRAMES:
            raise DbError('plan', f'the optimizer needs at least {MIN_FRAMES} frames, like Module 2')
        self.tables = tables            # {name: [column names]}
        self.stats = stats              # {name: TableStats}
        self.frames = frames
        self.rules = frozenset(rules)
        self.reorder = reorder
        self.join_algorithm = join_algorithm
        self.aggregate_mode = aggregate_mode
        self.dp_tables = []             # one per join block, for opt dp-table

    def plan(self, logical: dict) -> dict:
        return self.build(rewrite(logical, self.tables, self.rules)).annotated()

    # Nodes ----------------------------------------------------------------

    def build(self, node: dict) -> Built:
        from .enumerate import JoinBlock
        op = node['op']
        if op in ('join', 'cross') or (op == 'select' and self.absorbs_selects() and self.over_join(node)):
            return JoinBlock(self, node).build()
        if op == 'scan' or (op == 'rename' and node['child']['op'] == 'scan'):
            return self.scan(node)
        if op in lg.BINARY:
            return self.set_operation(node)
        child = self.build(node['child'])
        handler = getattr(self, f'build_{op}')
        return handler(node, child)

    def absorbs_selects(self) -> bool:
        return 2 in self.rules and 3 in self.rules

    def over_join(self, node: dict) -> bool:
        while node['op'] == 'select':
            node = node['child']
        return node['op'] in ('join', 'cross')

    def scan(self, node: dict) -> Built:
        table = node['table'] if node['op'] == 'scan' else node['child']['table']
        alias = None if node['op'] == 'scan' else node['to']
        stats = self.stats.get(table) or TableStats(table, 0, 0, 0.0)
        estimate = est.table_estimate(stats, self.tables[table], alias or table)
        plan = {'op': 'seqscan', 'table': table, **({'alias': alias} if alias else {})}
        return Built(plan, estimate, estimate.pages)

    def unary(self, node: dict, child: Built, estimate, own_cost: int = 0, **fields) -> Built:
        plan = {**fields, 'child': child.annotated()}
        return Built(plan, estimate, child.cost + own_cost, child.is_base)

    def build_select(self, node: dict, child: Built) -> Built:
        condition = lg.parse(node['condition'])
        return self.unary(node, child, est.filtered(child.est, condition), op='filter', condition=node['condition'])

    def build_project(self, node: dict, child: Built) -> Built:
        fields = {'op': 'project', 'columns': node['columns']}
        if node.get('aliases'):
            fields['aliases'] = node['aliases']
        return self.unary(node, child, est.projected(child.est, node['columns'], node.get('aliases')), **fields)

    def build_rename(self, node: dict, child: Built) -> Built:
        return self.unary(node, child, est.renamed(child.est, node['to']), op='rename', to=node['to'])

    def build_sort(self, node: dict, child: Built) -> Built:
        own = costs.sort_io(child.est.pages, self.frames)
        return self.unary(node, child, child.est, own, op='sort', keys=node['keys'])

    def build_limit(self, node: dict, child: Built) -> Built:
        return self.unary(node, child, est.limited(child.est, node['n']), op='limit', n=node['n'])

    def choose_mode(self, pages: int) -> tuple[str, int]:
        """Cheaper of sort and hash for distinct or group_by; hash wins ties."""
        options = {'hash': costs.hash_aggregate_io(pages, self.frames), 'sort': costs.sort_io(pages, self.frames)}
        if self.aggregate_mode:
            return self.aggregate_mode, options[self.aggregate_mode]
        mode = min(options, key=lambda m: (options[m], m != 'hash'))
        return mode, options[mode]

    def build_distinct(self, node: dict, child: Built) -> Built:
        mode, own = self.choose_mode(child.est.pages)
        return self.unary(node, child, est.deduplicated(child.est), own, op='distinct', mode=mode)

    def build_group_by(self, node: dict, child: Built) -> Built:
        aggregates = [list(a) if len(a) == 3 else [a[0], a[1], lg.default_aggregate_name(a)] for a in node['aggregates']]
        mode, own = self.choose_mode(child.est.pages) if node['keys'] else ('hash', 0)
        estimate = est.grouped(child.est, node['keys'], aggregates)
        return self.unary(node, child, estimate, own, op='group_by', mode=mode, keys=node['keys'],
                          aggregates=aggregates)

    def set_operation(self, node: dict) -> Built:
        left, right = self.build(node['left']), self.build(node['right'])
        op = node['op']
        own = 0
        if op == 'union':
            own = costs.hash_aggregate_io(left.est.pages + right.est.pages, self.frames)
        elif op in ('intersect', 'except'):
            own = costs.sort_merge_extra(left.est.pages, right.est.pages, self.frames)   # same frame split
        plan = {'op': op, 'left': left.annotated(), 'right': right.annotated()}
        return Built(plan, est.set_operation(op, left.est, right.est), left.cost + right.cost + own, False)

    # Joins ----------------------------------------------------------------

    def join_candidates(self, outer: Built, inner: Built, predicates: list, estimate=None) -> list[tuple[str, Built]]:
        """Every algorithm that can join these inputs on these predicates."""
        texts = sorted({lg.text(p) for p in predicates})
        nodes = [lg.parse(t) for t in texts]
        condition = lg.conjoin(nodes) if nodes else None
        left_names, right_names = list(outer.est.columns), list(inner.est.columns)
        equalities = [p for p in nodes if lg.is_equality_between(p, left_names, right_names)]
        others = [p for p in nodes if p not in equalities]
        joined = estimate or est.joined(outer.est, inner.est, condition)
        algorithms = [self.join_algorithm] if self.join_algorithm else ALGORITHMS
        if not equalities:
            algorithms = ['nested_loop_join']
        candidates = []
        for algorithm in algorithms:
            candidates.append((algorithm, self.join_node(algorithm, outer, inner, condition, equalities, others, joined)))
        return candidates

    def join_node(self, algorithm, outer: Built, inner: Built, condition, equalities, others, joined) -> Built:
        m, n = outer.est.pages, inner.est.pages
        reread = (0 if outer.is_base else m) + (0 if inner.is_base else n)
        if algorithm == 'nested_loop_join':
            own = reread + costs.nested_loop_extra(m, n, inner.plan['op'] == 'seqscan', self.frames)
            plan = {'op': algorithm, **({'condition': lg.text(condition)} if condition else {}),
                    'left': outer.annotated(), 'right': inner.annotated()}
            return Built(plan, joined, outer.cost + inner.cost + own, False)
        extra = costs.hash_join_extra(m, n, self.frames) if algorithm == 'hash_join' else \
            costs.sort_merge_extra(m, n, self.frames)
        plan = {'op': algorithm, 'condition': lg.text(lg.conjoin(equalities)),
                'left': outer.annotated(), 'right': inner.annotated()}
        if not others:
            return Built(plan, joined, outer.cost + inner.cost + reread + extra, False)
        on_keys = est.joined(outer.est, inner.est, lg.conjoin(equalities))
        middle = Built(plan, on_keys, outer.cost + inner.cost + reread + extra, False)
        filtered = {'op': 'filter', 'condition': lg.text(lg.conjoin(others)), 'child': middle.annotated()}
        return Built(filtered, joined, middle.cost, False)


def physical_plan(logical: dict, tables: dict, stats: dict, frames: int, **options) -> dict:
    return Planner(tables, stats, frames, **options).plan(logical)
