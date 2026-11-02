"""Write deterministic R(a,b) and S(b,c) inputs with an exact match count."""

import argparse
import sys


def generate(stream, r_rows: int, s_rows: int, matches: int) -> None:
    if r_rows < 0 or s_rows < 0:
        raise ValueError('row counts must be nonnegative')
    if matches < 0 or matches > s_rows:
        raise ValueError('matches must be between 0 and the number of S rows')
    stream.write('R(a,b) = {\n')
    for i in range(r_rows):
        stream.write(f'  {i}, 0\n')
    stream.write('}\nS(b,c) = {\n')
    for i in range(s_rows):
        key = 0 if i < matches else 1
        stream.write(f'  {key}, {i}\n')
    stream.write('}\n')


def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument('--r-rows', type=int, required=True)
    arguments.add_argument('--s-rows', type=int, required=True)
    arguments.add_argument('--matches', type=int, default=1,
                           help='S rows matching each R row, from 0 to s-rows (default: 1)')
    arguments.add_argument('--output', required=True, metavar='FILE')
    args = arguments.parse_args()
    if args.r_rows < 0 or args.s_rows < 0:
        arguments.error('row counts must be nonnegative')
    if not 0 <= args.matches <= args.s_rows:
        arguments.error('--matches must be between 0 and --s-rows')
    try:
        # Exclusive creation avoids overwriting an existing input file.
        with open(args.output, 'x', encoding='utf-8') as stream:
            generate(stream, args.r_rows, args.s_rows, args.matches)
    except OSError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
