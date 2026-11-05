"""The seven rewrite rules (Section 3), applied before costing.

    1  split select[p and q] into stacked selects
    2  push a select below a join or cross when it uses one side only
    3  turn cross plus an equality select over both sides into a join
    4  push a select below a rename (not one over a bare scan), a union or
       union_all, or a project that only passes columns through
    5  push a select below a group_by when it uses grouping columns only
    6  put projections below joins and crosses keeping only needed columns
    7  drop projections that keep everything, merge adjacent selects

`rules` says which are on, so the report can turn them off one at a time.
"""

from . import logical as lg

ALL_RULES = frozenset(range(1, 8))


class Rewriter:
    def __init__(self, tables: dict, rules=ALL_RULES) -> None:
        self.tables = tables
        self.rules = frozenset(rules)

    def run(self, plan: dict) -> dict:
        plan = self.push_selects(plan)
        if 6 in self.rules:
            plan = self.push_projections(plan, None)
        if 7 in self.rules:
            plan = self.tidy(plan)
        return plan

    def out(self, plan: dict) -> list[str]:
        return lg.outputs(plan, self.tables)

    # Rules 1 to 5 -------------------------------------------------------

    def push_selects(self, plan: dict) -> dict:
        """Bottom up: rewrite the children, then sink this node's predicates."""
        new = {**plan, **{key: self.push_selects(plan[key]) for key in lg.children(plan)}}
        if new['op'] != 'select':
            return new
        condition = lg.parse(new['condition'])
        pieces = lg.conjuncts(condition) if 1 in self.rules else [condition]
        result = new['child']
        for piece in reversed(pieces):
            result = self.sink(piece, result)
        return result

    def sink(self, condition, node: dict) -> dict:
        """Place `condition` as low under `node` as the enabled rules allow."""
        names = lg.attributes(condition)
        op = node['op']
        if op == 'select':
            return {**node, 'child': self.sink(condition, node['child'])}
        if op in ('join', 'cross'):
            return self.sink_into_join(condition, node, names)
        if 4 in self.rules and op == 'rename' and node['child']['op'] != 'scan':
            mapping = dict(zip(self.out(node), self.out(node['child'])))
            return {**node, 'child': self.sink(lg.rename_attributes(condition, mapping), node['child'])}
        if 4 in self.rules and op in ('union', 'union_all'):
            mapping = dict(zip(self.out(node['left']), self.out(node['right'])))
            return {**node, 'left': self.sink(condition, node['left']),
                    'right': self.sink(lg.rename_attributes(condition, mapping), node['right'])}
        if 4 in self.rules and op == 'project' and names <= set(self.out(node)):
            mapping = dict(zip(self.out(node), node['columns']))
            return {**node, 'child': self.sink(lg.rename_attributes(condition, mapping), node['child'])}
        if 5 in self.rules and op == 'group_by' and names <= set(node['keys']):
            return {**node, 'child': self.sink(condition, node['child'])}
        return lg.select(node, condition)

    def sink_into_join(self, condition, node: dict, names: set) -> dict:
        left, right = self.out(node['left']), self.out(node['right'])
        if 2 in self.rules and names <= set(left):
            return {**node, 'left': self.sink(condition, node['left'])}
        if 2 in self.rules and names <= set(right):
            return {**node, 'right': self.sink(condition, node['right'])}
        if 3 in self.rules and lg.is_equality_between(condition, left, right):
            if node['op'] == 'cross':
                return {'op': 'join', 'condition': lg.text(condition), 'left': node['left'], 'right': node['right']}
            joined = lg.conjoin([lg.parse(node['condition']), condition])
            return {**node, 'condition': lg.text(joined)}
        return lg.select(node, condition)

    # Rule 6 -------------------------------------------------------------

    def push_projections(self, plan: dict, needed: set | None) -> dict:
        """`needed` is what the ancestors read from this node; None means everything."""
        op = plan['op']
        if op == 'scan':
            return plan
        if op in ('join', 'cross'):
            here = (set(self.out(plan)) if needed is None else set(needed))
            if op == 'join':
                here |= lg.attributes(lg.parse(plan['condition']))
            return {**plan, 'left': self.narrow(plan['left'], here), 'right': self.narrow(plan['right'], here)}
        child_needs = self.child_needs(plan, needed)
        return {**plan, **{key: self.push_projections(plan[key], child_needs[key]) for key in lg.children(plan)}}

    def narrow(self, child: dict, needed: set) -> dict:
        columns = self.out(child)
        keep = [c for c in columns if c in needed]
        inner = self.push_projections(child, set(keep))
        if keep == columns or not keep:
            return inner
        return lg.project(inner, keep)

    def child_needs(self, plan: dict, needed: set | None) -> dict:
        op = plan['op']
        if op == 'project':
            return {'child': set(plan['columns'])}
        if op == 'select':
            return {'child': None if needed is None else needed | lg.attributes(lg.parse(plan['condition']))}
        if op == 'group_by':
            return {'child': set(plan['keys']) | {a[1] for a in plan['aggregates'] if a[1] != '*'}}
        if op == 'sort':
            return {'child': None if needed is None else needed | {k[0] for k in plan['keys']}}
        if op == 'limit':
            return {'child': needed}
        if op == 'rename' and needed is not None:
            mapping = dict(zip(self.out(plan), self.out(plan['child'])))
            return {'child': {mapping[c] for c in needed if c in mapping}}
        return {key: None for key in lg.children(plan)}   # distinct, set operators, rename of everything

    # Rule 7 -------------------------------------------------------------

    def tidy(self, plan: dict) -> dict:
        plan = {**plan, **{key: self.tidy(plan[key]) for key in lg.children(plan)}}
        if plan['op'] == 'project' and not plan.get('aliases') and plan['columns'] == self.out(plan['child']):
            return plan['child']
        if plan['op'] == 'select' and plan['child']['op'] == 'select':
            merged = lg.conjoin([lg.parse(plan['condition']), lg.parse(plan['child']['condition'])])
            return lg.select(plan['child']['child'], merged)
        return plan


def rewrite(plan: dict, tables: dict, rules=ALL_RULES) -> dict:
    return Rewriter(tables, rules).run(plan)
