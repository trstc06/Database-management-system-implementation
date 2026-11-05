"""The `sql` command-line tool (Module 3, Section 8).

    sql parse   --query "SELECT ..." --explain        indented parse tree, no catalog needed
    sql compile --db DIR --file q.sql --json          logical plan JSON (a list if several statements)
    sql check   --db DIR --file q.sql                 errors only, exit code 0 or 1

--catalog is accepted as another name for --db, and either may point at a
database folder or a JSON catalog file.
"""

import argparse
import json
import sys
from pathlib import Path
from ..common.errors import DbError
from .explain import explain_plan, explain_statement
from .parser import parse
from .scope import load_catalog
from .translate import compile_sql


def read_source(args) -> str:
    if args.query is not None:
        return args.query
    if args.file is None:
        raise DbError('syntax', 'give the SQL with --query or --file')
    return Path(args.file).read_text(encoding='utf-8')


def arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='sql', description='Parse and compile SQL to logical plans.')
    parser.add_argument('command', choices=('parse', 'compile', 'check'))
    parser.add_argument('--query')
    parser.add_argument('--file')
    parser.add_argument('--db', '--catalog', dest='db')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--explain', action='store_true')
    return parser


def run(args) -> str | None:
    source = read_source(args)
    if args.command == 'parse':
        statements = parse(source)
        return '\n\n'.join('\n'.join(explain_statement(s)) for s in statements)
    if args.db is None:
        raise DbError('name', f'sql {args.command} needs --db DIR to look up tables')
    plans = compile_sql(source, load_catalog(args.db))
    if args.command == 'check':
        return None
    if args.json and not args.explain:
        return json.dumps(plans[0] if len(plans) == 1 else plans, indent=2)
    return '\n\n'.join('\n'.join(explain_plan(p)) for p in plans)


def main() -> int:
    args = arguments().parse_args()
    try:
        output = run(args)
    except DbError as error:
        print(error.unified(), file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as error:
        print(f'error: schema: {error}', file=sys.stderr)
        return 1
    if output:
        print(output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
