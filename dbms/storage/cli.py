"""The `storage` command-line tool (Module 1, Section 7).

    storage create-table --db DIR --name R --schema "a:INT,b:VARCHAR(20),c:INT"
    storage load         --db DIR --table R --file r.csv
    storage scan         --db DIR --table R --frames 8 [--limit N] --json
    storage layout       --db DIR --table R --page 3
    storage trace        --db DIR --file trace.txt --frames 16 --policy lru|clock --json
"""

import argparse
import itertools
import json
import sys
from pathlib import Path
from ..common.errors import DbError
from ..common.schema import INT, VARCHAR, Column
from .buffer import BufferPool
from .database import Database
from .disk import NullDisk
from .layout import describe_page, format_layout
from .loader import load_csv

DEFAULT_FRAMES = 8


def parse_schema(text: str, table: str) -> tuple[Column, ...]:
    """Read "a:INT,b:VARCHAR(20)" by hand."""
    columns = []
    for part in text.split(','):
        name, colon, kind = part.strip().partition(':')
        kind = kind.strip()
        if not colon or not name:
            raise DbError('schema', f'column definition {part.strip()!r} must look like name:TYPE')
        upper = kind.upper()
        if upper == INT:
            columns.append(Column(table, name.strip(), INT))
        elif upper.startswith('VARCHAR(') and upper.endswith(')') and kind[8:-1].isdigit():
            columns.append(Column(table, name.strip(), VARCHAR, int(kind[8:-1])))
        else:
            raise DbError('schema', f'column {name.strip()} has type {kind!r}; use INT or VARCHAR(n)')
    return tuple(columns)


def schema_json(columns: tuple[Column, ...]) -> list:
    return [[c.name, c.type] if c.type == INT else [c.name, c.type, c.length] for c in columns]


def read_trace(path: str) -> list[int]:
    pages = []
    with open(path, encoding='utf-8') as stream:
        for line_no, line in enumerate(stream, start=1):
            text = line.strip()
            if text == '':
                continue
            if not text.isdigit():
                raise DbError('load', f'trace line {line_no}: {text!r} is not a page id')
            pages.append(int(text))
    return pages


def create_table(args) -> dict:
    with Database(Path(args.db), frames=args.frames, create=True) as db:
        info = db.create_table(args.name, parse_schema(args.schema, args.name))
        return {'table': info.name, 'schema': schema_json(info.columns), 'file': info.file}


def load(args) -> dict:
    with Database(Path(args.db), frames=args.frames, policy=args.policy) as db:
        loaded = load_csv(db, args.table, Path(args.file))
        info = db.catalog.get_table(args.table)
        return {'table': info.name, 'rows_loaded': loaded, 'num_pages': info.num_pages,
                'num_tuples': info.num_tuples, 'stats': db.pool.stats()}


def scan(args) -> dict:
    with Database(Path(args.db), frames=args.frames, policy=args.policy) as db:
        table = db.table(args.table)
        rows = table.rows()
        limited = rows if args.limit is None else itertools.islice(rows, args.limit)
        output = [list(row) for row in limited]
        rows.close()  # stops the scan, which unpins its page
        return {'table': table.info.name, 'schema': schema_json(table.columns),
                'rows': output, 'stats': db.pool.stats()}


def layout(args) -> dict:
    with Database(Path(args.db), frames=args.frames) as db:
        return {'table': args.table, **describe_page(db.table(args.table), args.page)}


def trace(args) -> dict:
    pool, disk = BufferPool(args.frames, args.policy), NullDisk()
    for page_id in read_trace(args.file):
        pool.fetch_page(disk, page_id)
        pool.unpin_page(disk, page_id, False)
    return {'policy': args.policy, 'frames': args.frames, **pool.stats()}


def format_text(command: str, result: dict) -> str:
    if command == 'layout':
        return format_layout(result['table'], result)
    if command == 'scan':
        lines = ['\t'.join(column[0] for column in result['schema'])]
        lines += ['\t'.join(str(value) for value in row) for row in result['rows']]
        lines.append(' '.join(f'{key}={value}' for key, value in result['stats'].items()))
        return '\n'.join(lines)
    if command == 'create-table':
        columns = ', '.join(' '.join(map(str, column)) for column in result['schema'])
        return f"created table {result['table']} ({columns}) in {result['file']}"
    stats = result.get('stats', result)
    counters = ' '.join(f'{key}={stats[key]}' for key in ('hits', 'misses', 'evictions', 'reads', 'writes'))
    if command == 'load':
        return (f"loaded {result['rows_loaded']} rows into {result['table']}: "
                f"{result['num_tuples']} tuples on {result['num_pages']} data pages\n{counters}")
    return f"policy={result['policy']} frames={result['frames']} {counters}"


def arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='storage', description='Module 1 storage engine.')
    commands = parser.add_subparsers(dest='command', required=True)
    specs = {
        'create-table': (create_table, [('--name', {'required': True}), ('--schema', {'required': True})]),
        'load': (load, [('--table', {'required': True}), ('--file', {'required': True})]),
        'scan': (scan, [('--table', {'required': True}), ('--limit', {'type': int})]),
        'layout': (layout, [('--table', {'required': True}), ('--page', {'type': int, 'required': True})]),
        'trace': (trace, [('--file', {'required': True})]),
    }
    for name, (handler, options) in specs.items():
        command = commands.add_parser(name)
        command.add_argument('--db', required=name != 'trace', help='database directory')
        command.add_argument('--frames', type=int, default=DEFAULT_FRAMES)
        command.add_argument('--policy', choices=['lru', 'clock'], default='lru')
        command.add_argument('--json', action='store_true')
        for flag, settings in options:
            command.add_argument(flag, **settings)
        command.set_defaults(handler=handler)
    return parser


def main() -> int:
    args = arguments().parse_args()
    try:
        if args.frames < 1:
            raise DbError('storage', '--frames must be at least 1')
        result = args.handler(args)
    except DbError as error:
        print(error.unified(), file=sys.stderr)
        return 1
    except OSError as error:
        print(f'error: storage: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else format_text(args.command, result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
