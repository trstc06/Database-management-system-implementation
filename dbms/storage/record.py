"""Record format: field encodings concatenated in schema order.

No record header and no null bitmap (there are no NULLs). All integers are
little-endian.

    INT          4 bytes, signed two's complement
    VARCHAR(n)   2 byte unsigned length L, then L bytes of UTF-8, L <= n <= 255
"""

import struct
from ..common.errors import DbError
from ..common.schema import INT, MAX_VARCHAR, VARCHAR, Column

INT_FIELD = struct.Struct('<i')
LENGTH_FIELD = struct.Struct('<H')
INT_MIN, INT_MAX = -2 ** 31, 2 ** 31 - 1


def field_problem(value: object, column: Column) -> str | None:
    """Why `value` cannot be stored in `column`, or None when it can."""
    if column.type == INT:
        if not isinstance(value, int) or isinstance(value, bool):
            return f'{column.name} expects an INT'
        if not INT_MIN <= value <= INT_MAX:
            return f'{column.name} value {value} is outside the 4 byte INT range'
        return None
    if not isinstance(value, str):
        return f'{column.name} expects a VARCHAR'
    limit = column.length if column.length is not None else MAX_VARCHAR
    size = len(value.encode('utf-8'))
    if size > limit:
        return f'{column.name} value is {size} bytes, longer than VARCHAR({limit})'
    return None


def encode_record(values: tuple, columns: tuple[Column, ...]) -> bytes:
    if len(values) != len(columns):
        raise DbError('storage', f'expected {len(columns)} values, got {len(values)}')
    parts = []
    for value, column in zip(values, columns):
        problem = field_problem(value, column)
        if problem is not None:
            raise DbError('storage', problem)
        if column.type == INT:
            parts.append(INT_FIELD.pack(value))
        else:
            data = value.encode('utf-8')
            parts.append(LENGTH_FIELD.pack(len(data)) + data)
    return b''.join(parts)


def decode_record(data: bytes, columns: tuple[Column, ...]) -> tuple:
    values = []
    offset = 0
    for column in columns:
        if column.type == INT:
            values.append(INT_FIELD.unpack_from(data, offset)[0])
            offset += INT_FIELD.size
        else:
            (length,) = LENGTH_FIELD.unpack_from(data, offset)
            offset += LENGTH_FIELD.size
            values.append(bytes(data[offset:offset + length]).decode('utf-8'))
            offset += length
    if offset != len(data):
        raise DbError('storage', f'record is {len(data)} bytes but its schema reads {offset}')
    return tuple(values)


def minimum_length(columns: tuple[Column, ...]) -> int:
    """Shortest possible record: INTs are 4 bytes, an empty VARCHAR is 2."""
    return sum(INT_FIELD.size if column.type == INT else LENGTH_FIELD.size for column in columns)


def check_type(column: Column) -> None:
    if column.type not in (INT, VARCHAR):
        raise DbError('schema', f'column {column.name} has unknown type {column.type}')
    if column.type == VARCHAR and not (column.length is not None and 1 <= column.length <= MAX_VARCHAR):
        raise DbError('schema', f'column {column.name} must be VARCHAR(n) with 1 <= n <= {MAX_VARCHAR}')
