"""The `dbms` shell: one program with a prompt, where SQL goes in and rows come out.

    dbms --db DIR [--frames 32]                    interactive prompt (or SQL piped on stdin)
    dbms --db DIR --file script.sql                run every statement in a file
    dbms --db DIR --workload queries.sql [--out report.md]
                                                   per query: rows, page I/O, wall time, est vs actual root rows

Statements end with a semicolon. Type quit or exit (or Ctrl-D) to leave.
"""

import argparse
import sys
from pathlib import Path
from ..common.errors import DbError
from .session import DEFAULT_FRAMES, Session
from .statements import is_blank, split_statements
from .workload import format_report, run_workload

PROMPT, CONTINUE = 'dbms> ', '  ...> '
QUIT_WORDS = ('quit', 'exit', '\\q')


def run_statement(session: Session, text: str) -> bool:
    """Print the statement's output or its error. Returns False on an error."""
    try:
        print(session.execute(text), flush=True)
        return True
    except DbError as error:
        print(error.unified(), file=sys.stderr)
        return False
    except OSError as error:
        print(f'error: schema: {error}', file=sys.stderr)
        return False


def run_text(session: Session, text: str) -> bool:
    statements, rest = split_statements(text)
    if not is_blank(rest):
        statements.append(rest)
    ok = True
    for statement in statements:
        ok = run_statement(session, statement) and ok
    return ok


def interactive(session: Session) -> int:
    tty = sys.stdin.isatty()
    buffer, ok = '', True
    while True:
        if tty:
            sys.stdout.write(CONTINUE if buffer.strip() else PROMPT)
            sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            break
        if not buffer.strip() and line.strip().rstrip(';').lower() in QUIT_WORDS:
            break
        statements, buffer = split_statements(buffer + line)
        for statement in statements:
            ok = run_statement(session, statement) and ok
        if is_blank(buffer):
            buffer = ''   # positions in the next statement count from its own first line
    if not is_blank(buffer):
        ok = run_statement(session, buffer) and ok
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog='dbms', description='SQL shell over the whole system.')
    parser.add_argument('--db', required=True, help='database folder (created if it does not exist)')
    parser.add_argument('--frames', type=int, default=DEFAULT_FRAMES)
    parser.add_argument('--file', help='run the statements in this file')
    parser.add_argument('--workload', help='run these queries and print the per query report')
    parser.add_argument('--out', help='also write the workload report to this file')
    args = parser.parse_args()
    try:
        session = Session(Path(args.db))
        session.set_option(f'frames = {args.frames}')
        if args.workload:
            report = format_report(run_workload(session, Path(args.workload)), session.frames)
            print(report)
            if args.out:
                Path(args.out).write_text(report + '\n', encoding='utf-8')
            return 0
        if args.file:
            return 0 if run_text(session, Path(args.file).read_text(encoding='utf-8')) else 1
    except DbError as error:
        print(error.unified(), file=sys.stderr)
        return 1
    except OSError as error:
        print(f'error: schema: {error}', file=sys.stderr)
        return 1
    return interactive(session)


if __name__ == '__main__':
    sys.exit(main())
