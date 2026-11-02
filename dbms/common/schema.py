"""Columns, types and column-name resolution shared by every module.

Every column has a qualifier (its table or alias) and a name, so its
qualified name `t.c` is always available. `show_qualifier` only affects how
the relational algebra layer prints a column, keeping the Bonus 1 display
rule: base-table columns print bare until a product makes them ambiguous.
"""

from dataclasses import dataclass
from .errors import ExecutionError

INT = 'INT'
VARCHAR = 'VARCHAR'
MAX_VARCHAR = 255


@dataclass(frozen=True)
class Column:
    qualifier: str
    name: str
    type: str | None = None  # INT, VARCHAR, or None when no value revealed it
    length: int | None = None  # VARCHAR(n) bound; None when unbounded
    show_qualifier: bool = False

    @property
    def qualified_name(self) -> str:
        return f'{self.qualifier}.{self.name}'

    @property
    def label(self) -> str:
        return self.qualified_name if self.show_qualifier else self.name


def type_label(column_type: str | None) -> str:
    """Name a type in messages; keeps Bonus 1's number/string wording."""
    return {INT: 'INT (number)', VARCHAR: 'VARCHAR (string)'}.get(column_type, 'unknown')


def check_unique(columns: tuple[Column, ...]) -> None:
    """Reject two columns with the same qualified name in one schema."""
    seen = set()
    for column in columns:
        if column.qualified_name in seen:
            raise ExecutionError('Schema', f'duplicate column {column.label}; use distinct names or rename the inputs')
        seen.add(column.qualified_name)


def resolve(name: str, columns: tuple[Column, ...]) -> int:
    """Return the index of `name` (qualified `t.c` or bare `c`) in `columns`."""
    matches = []
    for index, column in enumerate(columns):
        candidate = column.qualified_name if '.' in name else column.name
        if candidate == name:
            matches.append(index)
    if not matches:
        raise ExecutionError('Name', f'unknown attribute {name}')
    if len(matches) != 1:
        options = ', '.join(columns[i].qualified_name for i in matches)
        raise ExecutionError('Name', f'ambiguous attribute {name}; qualify it with a relation name ({options})')
    return matches[0]
