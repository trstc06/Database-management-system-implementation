"""The `exec` command-line tool (Module 2, Section 4).

    exec run   --db DIR --plan plan.json --frames 32 [--policy lru|clock] --json
    exec trace --db DIR --plan plan.json --frames 32
"""

import argparse
import json
import sys
from pathlib import Path
from ..common.errors import DbError
from ..storage.database import Database
from .run import run_plan

DEFAULT_FRAMES = 32


def read_plan(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise DbError('plan', f'{path} is not valid JSON: {error.msg} at line {error.lineno}') from None


def format_rows(result) -> str:
    header = [name for name, *_ in result.schema()]
    lines = [' | '.join(header)]
    for row in result.rows:
        lines.append(' | '.join(f'{v:.4f}' if isinstance(v, float) else str(v) for v in row))
    lines.append(f'({len(result.rows)} rows)')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog='exec', description='Run a physical plan.')
    parser.add_argument('command', choices=('run', 'trace'))
    parser.add_argument('--db', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--frames', type=int, default=DEFAULT_FRAMES)
    parser.add_argument('--policy', default='lru')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        if args.frames < 1:
            raise DbError('plan', '--frames must be at least 1')
        plan = read_plan(args.plan)
        with Database(Path(args.db), frames=args.frames, policy=args.policy) as db:
            result = run_plan(db, plan, args.frames)
    except DbError as error:
        print(error.unified(), file=sys.stderr)
        return 1
    except OSError as error:
        print(f'error: execution: {error}', file=sys.stderr)
        return 1
    if args.command == 'trace':
        print(result.trace())
    elif args.json:
        print(json.dumps(result.to_json()))
    else:
        print(format_rows(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
