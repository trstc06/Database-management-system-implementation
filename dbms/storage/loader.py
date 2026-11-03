"""CSV bulk loader, hand-written (no csv module, no regular expressions).

One record per line, no header, fields separated by commas. A field that
contains a comma must be wrapped in double quotes; inside quotes, two
double quotes stand for one. Blank lines are skipped, so a one-column
VARCHAR empty string must be written "". The file is checked completely
before the first insert, so a bad line leaves the table unchanged.
"""

from pathlib import Path
from typing import Iterator
from ..common.errors import DbError
from ..common.schema import INT, Column
from .database import Database, Table
from .page import MAX_RECORD
from .record import encode_record, field_problem


def load_error(line_no: int, message: str) -> DbError:
    return DbError('load', f'line {line_no}: {message}')


def split_fields(line: str, line_no: int) -> list[str]:
    fields = []
    i = 0
    while True:
        if i < len(line) and line[i] == '"':
            i += 1
            characters = []
            while True:
                if i >= len(line):
                    raise load_error(line_no, f'column {len(fields) + 1}: unterminated quoted field')
                if line[i] == '"':
                    if i + 1 < len(line) and line[i + 1] == '"':
                        characters.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                characters.append(line[i])
                i += 1
            if i < len(line) and line[i] != ',':
                raise load_error(line_no, f'column {len(fields) + 1}: expected a comma after the closing quote')
            fields.append(''.join(characters))
        else:
            start = i
            while i < len(line) and line[i] != ',':
                i += 1
            fields.append(line[start:i])
        if i >= len(line):
            return fields
        i += 1  # skip the comma


def parse_int(text: str) -> int | None:
    digits = text[1:] if text[:1] in ('-', '+') else text
    if digits == '' or not all('0' <= c <= '9' for c in digits):
        return None
    return int(text)


def convert(fields: list[str], columns: tuple[Column, ...], line_no: int) -> tuple:
    if len(fields) != len(columns):
        raise load_error(line_no, f'expected {len(columns)} fields, got {len(fields)}')
    values = []
    for position, (text, column) in enumerate(zip(fields, columns), start=1):
        where = f'column {position} ({column.name})'
        value = text
        if column.type == INT:
            value = parse_int(text)
            if value is None:
                raise load_error(line_no, f'{where}: {text!r} is not an INT')
        problem = field_problem(value, column)
        if problem is not None:
            raise load_error(line_no, f'{where}: {problem}')
        values.append(value)
    return tuple(values)


def records(path: Path, table: Table) -> Iterator[tuple[int, tuple]]:
    """Yield (line number, values) for every non-blank line, checking each."""
    with open(path, encoding='utf-8', newline='') as stream:
        for line_no, raw in enumerate(stream, start=1):
            line = raw.rstrip('\r\n')
            if line == '':
                continue
            values = convert(split_fields(line, line_no), table.columns, line_no)
            size = len(encode_record(values, table.columns))
            if size > MAX_RECORD:
                raise DbError('load', f'table {table.info.name}, line {line_no}: record is {size} bytes, longer than the {MAX_RECORD} bytes a page can hold')
            yield line_no, values


def load_csv(database: Database, table_name: str, path: Path) -> int:
    """Append every row of the CSV file; returns the number of rows loaded."""
    table = database.table(table_name)
    for _ in records(path, table):
        pass  # first pass: validate only
    loaded = 0
    for _, values in records(path, table):
        table.insert(values)
        loaded += 1
    database.save_counts(table, table.info.num_tuples + loaded)
    return loaded
