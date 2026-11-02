"""Error types shared by every module.

Each error carries a lowercase category from the M3 list (lexical, syntax,
name, ambiguity, type, aggregate, schema) or a module-specific one such as
storage or plan. The string form keeps the Bonus 1 wording so the original
cases still read the same; `unified` gives the M3 one-line format.
"""


class DbError(Exception):
    def __init__(self, category: str, message: str, line: int | None = None,
                 column: int | None = None) -> None:
        self.category = category
        self.message = message
        self.line = line
        self.column = column
        super().__init__(self.describe())

    def describe(self) -> str:
        return f'{self.category.capitalize()} error: {self.message}'

    def unified(self) -> str:
        text = f'error: {self.category}: {self.message}'
        if self.line is not None:
            text += f' at line {self.line}, column {self.column}'
        return text


class LexicalError(DbError):
    def __init__(self, message: str, offset: int, line: int, column: int) -> None:
        self.offset = offset
        super().__init__('lexical', message, line, column)

    def describe(self) -> str:
        return f'Lexical error at line {self.line}, column {self.column}: {self.message}'


class ParseError(DbError):
    def __init__(self, message: str, token) -> None:
        self.offset = token.offset
        super().__init__('syntax', message, token.line, token.column)

    def describe(self) -> str:
        return f'Syntax error at line {self.line}, column {self.column}: {self.message}'


class ExecutionError(DbError):
    """Name, schema and type errors found while binding or running a plan."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(category.lower(), message)
