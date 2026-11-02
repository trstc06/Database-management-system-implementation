"""Command-line program for the relational algebra layer.

This is the only module in the package that reads files or prints. The
library modules (tokenizer, parser, tree, operators) do neither.

    bin/ra --tree QUERY
    bin/ra --query QUERY --relations FILE [--stats]
    bin/ra --tokens QUERY
    bin/ra --tokens-file FILE
"""

import argparse
import json
import sys
from dataclasses import asdict
from ..common.errors import DbError
from .evaluate import execute, format_relation
from .operators import Statistics
from .parser import parse
from .relfile import load_relations
from .tokenizer import tokenize
from .tree import format_tree


def read_text(path: str) -> str:
    # Preserve CRLF so token offsets still match the original file.
    with open(path, encoding='utf-8', newline='') as stream:
        return stream.read()


def format_tokens(source: str, relations: bool) -> str:
    return '\n'.join(f'{token.line}:{token.column} {token.kind} {token.value!r}'
                     for token in tokenize(source, relations=relations))


def arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run relational algebra queries or print their trees.')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--tree', metavar='QUERY', help='print the tree without execution')
    modes.add_argument('--query', metavar='QUERY', help='execute against --relations FILE')
    modes.add_argument('--tokens', metavar='QUERY', help='print the tokens of a query')
    modes.add_argument('--tokens-file', metavar='FILE', help='print the tokens of a relation file')
    parser.add_argument('--relations', metavar='FILE')
    parser.add_argument('--stats', action='store_true', help='write measured query statistics as JSON to standard error')
    return parser


def run(args) -> tuple[str, Statistics | None]:
    if args.tree is not None:
        return format_tree(parse(args.tree)), None
    if args.tokens is not None:
        return format_tokens(args.tokens, relations=False), None
    if args.tokens_file is not None:
        return format_tokens(read_text(args.tokens_file), relations=True), None
    stats = Statistics() if args.stats else None
    return format_relation(execute(args.query, load_relations(read_text(args.relations)), stats)), stats


def main() -> int:
    parser = arguments()
    args = parser.parse_args()
    if args.stats and args.query is None:
        parser.error('--stats requires --query')
    if args.query is not None and args.relations is None:
        parser.error('--query requires --relations FILE')
    try:
        output, stats = run(args)
    except (DbError, OSError, UnicodeError) as error:
        print(error, file=sys.stderr)
        return 1
    print(output)
    if stats is not None:
        print(json.dumps(asdict(stats)), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
