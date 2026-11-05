"""The `opt` command-line tool (Module 4, Section 6).

    opt analyze  --db DIR --table R [--buckets 20]
    opt plan     --db DIR --logical q.json --frames 32 --json     physical plan
    opt explain  --db DIR --logical q.json --frames 32 [--rewrites]
    opt dp-table --db DIR --logical q.json --frames 32

--sql "SELECT ..." or --sql-file q.sql can stand in for --logical; the SQL
is compiled by Module 3 first. --disable-rule N (repeatable) turns a
rewrite rule off.
"""

import argparse
import json
import sys
from pathlib import Path
from ..common.errors import DbError
from ..sql.explain import explain_plan
from ..sql.scope import table_columns as qualified_columns
from ..sql.translate import compile_sql
from ..storage.database import Database
from .explain import explain_physical, format_dp_table
from .planner import Planner
from .rewrite import ALL_RULES, rewrite
from .stats import DEFAULT_BUCKETS, analyze, load_stats

DEFAULT_FRAMES = 32


def table_columns(db) -> dict:
    return {name: [c.name for c in db.catalog.get_table(name).columns] for name in db.catalog.list_tables()}


def sql_catalog(db) -> dict:
    return {name: qualified_columns(name, db.catalog.get_table(name).columns) for name in db.catalog.list_tables()}


def read_logical(args, db) -> dict:
    if args.logical:
        try:
            return json.loads(Path(args.logical).read_text(encoding='utf-8'))
        except json.JSONDecodeError as error:
            raise DbError('plan', f'{args.logical} is not valid JSON: {error.msg}') from None
    source = args.sql if args.sql is not None else Path(args.sql_file).read_text(encoding='utf-8') \
        if args.sql_file else None
    if source is None:
        raise DbError('plan', 'give a logical plan with --logical, or SQL with --sql or --sql-file')
    plans = compile_sql(source, sql_catalog(db))
    if len(plans) != 1:
        raise DbError('plan', f'expected one query, got {len(plans)} statements')
    return plans[0]


def arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='opt', description='Statistics and query optimization.')
    parser.add_argument('command', choices=('analyze', 'plan', 'explain', 'dp-table'))
    parser.add_argument('--db', required=True)
    parser.add_argument('--table')
    parser.add_argument('--buckets', type=int, default=DEFAULT_BUCKETS)
    parser.add_argument('--logical')
    parser.add_argument('--sql')
    parser.add_argument('--sql-file')
    parser.add_argument('--frames', type=int, default=DEFAULT_FRAMES)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--rewrites', action='store_true', help='also print the logical plan before and after rewriting')
    parser.add_argument('--disable-rule', type=int, action='append', default=[])
    return parser


def run(args) -> str:
    with Database(Path(args.db), frames=max(args.frames, 4)) as db:
        if args.command == 'analyze':
            if not args.table:
                raise DbError('name', 'opt analyze needs --table')
            records = analyze(db, args.table, args.buckets)
            columns = sum(1 for r in records if r[0] == 3)
            buckets = len(records) - columns
            return f'analyzed {args.table}: {columns} columns, {buckets} histogram buckets'
        logical = read_logical(args, db)
        rules = ALL_RULES - set(args.disable_rule)
        tables = table_columns(db)
        planner = Planner(tables, load_stats(db), args.frames, rules)
        physical = planner.plan(logical)
    if args.command == 'plan':
        return json.dumps(physical, indent=2) if args.json else '\n'.join(explain_physical(physical))
    if args.command == 'dp-table':
        if not planner.dp_tables:
            return 'this query has no joins, so there is no DP table'
        return '\n\n'.join(format_dp_table(rows) for rows in planner.dp_tables)
    lines = []
    if args.rewrites:
        lines += ['logical plan before rewriting:'] + explain_plan(logical)
        lines += ['', 'after rewriting:'] + explain_plan(rewrite(logical, tables, rules)) + ['', 'physical plan:']
    return '\n'.join(lines + explain_physical(physical))


def main() -> int:
    args = arguments().parse_args()
    try:
        print(run(args))
    except DbError as error:
        print(error.unified(), file=sys.stderr)
        return 1
    except OSError as error:
        print(f'error: plan: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
