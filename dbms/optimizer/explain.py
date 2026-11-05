"""Text output for physical plans and DP tables."""

INDENT = '  '


def node_label(plan: dict) -> str:
    op = plan['op']
    if op == 'seqscan':
        return f"seqscan {plan['table']}" + (f" as {plan['alias']}" if plan.get('alias') else '')
    if op in ('filter', 'nested_loop_join', 'sort_merge_join', 'hash_join'):
        return f"{op} ({plan['condition']})" if plan.get('condition') else f'{op} (cross product)'
    if op == 'project':
        aliases = plan.get('aliases') or [None] * len(plan['columns'])
        return 'project [' + ', '.join(c if a is None else f'{c} AS {a}' for c, a in zip(plan['columns'], aliases)) + ']'
    if op == 'rename':
        return f"rename {plan['to']}"
    if op == 'sort':
        return 'sort [' + ', '.join(f'{c} {d}' for c, d in plan['keys']) + ']'
    if op == 'group_by':
        aggregates = ', '.join(f'{f}({a}) AS {n}' for f, a, n in plan['aggregates'])
        return f"group_by {plan['mode']} keys [{', '.join(plan['keys'])}] [{aggregates}]"
    if op == 'distinct':
        return f"distinct {plan['mode']}"
    if op == 'limit':
        return f"limit {plan['n']}"
    return op


def explain_physical(plan: dict, depth: int = 0) -> list[str]:
    label = INDENT * depth + node_label(plan)
    lines = [f"{label:<52} est_rows={plan['est_rows']:<8} est_cost={plan['est_cost']}"]
    for key in ('child', 'left', 'right'):
        if key in plan:
            lines.extend(explain_physical(plan[key], depth + 1))
    return lines


def format_dp_table(rows) -> str:
    width = max(len(r.subset) for r in rows) + 2
    plan_width = max(len(r.plan) for r in rows) + 2
    lines = [f"{'Subset':<{width}}{'Best plan considered':<{plan_width}}Cost"]
    for r in rows:
        lines.append(f"{r.subset:<{width}}{r.plan:<{plan_width}}{'-' if r.cost is None else r.cost}")
    return '\n'.join(lines)
