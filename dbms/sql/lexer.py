"""Hand-written SQL scanner (Section 1). No regular expressions.

Keywords are case insensitive and come out upper case. Identifiers keep
their case. Numbers are integers with an optional sign, strings are single
quoted with '' for a quote, and -- starts a comment to the end of the line.
Operators use maximal munch, so <=, >= and <> are single tokens.
"""

from dataclasses import dataclass
from ..common.errors import DbError

KEYWORDS = frozenset({
    'SELECT', 'DISTINCT', 'FROM', 'WHERE', 'GROUP', 'BY', 'HAVING', 'ORDER', 'LIMIT', 'AS',
    'JOIN', 'ON', 'AND', 'OR', 'NOT', 'UNION', 'ALL', 'INTERSECT', 'EXCEPT', 'ASC', 'DESC',
    'CREATE', 'TABLE', 'INSERT', 'INTO', 'VALUES', 'INT', 'VARCHAR',
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX',
})
TWO_CHARACTER = ('<=', '>=', '<>')
ONE_CHARACTER = frozenset('=<>(),.;*')


@dataclass(frozen=True)
class Token:
    kind: str      # keyword, ident, int, string, symbol, end
    value: str
    line: int
    column: int

    def describe(self) -> str:
        if self.kind == 'end':
            return 'the end of the input'
        if self.kind == 'string':
            return f"string '{self.value}'"
        return f"'{self.value}'"


def lexical_error(message: str, line: int, column: int) -> DbError:
    return DbError('lexical', message, line, column)


def is_digit(char: str) -> bool:
    return '0' <= char <= '9'


def is_name_start(char: str) -> bool:
    return char == '_' or 'a' <= char <= 'z' or 'A' <= char <= 'Z'


def is_name_char(char: str) -> bool:
    return is_name_start(char) or is_digit(char)


class Scanner:
    def __init__(self, source: str) -> None:
        self.source = source
        self.i = 0
        self.line = 1
        self.column = 1

    def peek(self, ahead: int = 0) -> str:
        index = self.i + ahead
        return self.source[index] if index < len(self.source) else ''

    def advance(self) -> str:
        char = self.source[self.i]
        self.i += 1
        if char == '\n':
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def skip_space_and_comments(self) -> None:
        while self.i < len(self.source):
            char = self.peek()
            if char in ' \t\r\n':
                self.advance()
            elif char == '-' and self.peek(1) == '-':
                while self.i < len(self.source) and self.peek() != '\n':
                    self.advance()
            else:
                return

    def take_while(self, test) -> str:
        start = self.i
        while self.i < len(self.source) and test(self.peek()):
            self.advance()
        return self.source[start:self.i]

    def string(self, line: int, column: int) -> Token:
        self.advance()   # opening quote
        parts = []
        while True:
            if self.i >= len(self.source):
                raise lexical_error('string is never closed; add a closing quote', line, column)
            char = self.advance()
            if char != "'":
                parts.append(char)
            elif self.peek() == "'":
                parts.append(self.advance())
            else:
                return Token('string', ''.join(parts), line, column)

    def number(self, line: int, column: int) -> Token:
        sign = self.advance() if self.peek() in '+-' else ''
        digits = self.take_while(is_digit)
        if self.peek() == '.' and is_digit(self.peek(1)):
            raise lexical_error('only integer numbers are supported', line, column)
        if is_name_start(self.peek()):
            raise lexical_error(f"'{sign}{digits}{self.peek()}' is not a number or a name", line, column)
        return Token('int', ('-' if sign == '-' else '') + digits, line, column)

    def next_token(self) -> Token:
        self.skip_space_and_comments()
        line, column = self.line, self.column
        if self.i >= len(self.source):
            return Token('end', '', line, column)
        char = self.peek()
        if char == "'":
            return self.string(line, column)
        if is_digit(char) or (char in '+-' and is_digit(self.peek(1))):
            return self.number(line, column)
        if is_name_start(char):
            word = self.take_while(is_name_char)
            if word.upper() in KEYWORDS:
                return Token('keyword', word.upper(), line, column)
            return Token('ident', word, line, column)
        pair = char + self.peek(1)
        if pair in TWO_CHARACTER:
            self.advance()
            self.advance()
            return Token('symbol', pair, line, column)
        if char in ONE_CHARACTER:
            self.advance()
            return Token('symbol', char, line, column)
        if pair == '!=':
            raise lexical_error("'!=' is not SQL; write <> for not equal", line, column)
        raise lexical_error(f"unexpected character '{char}'", line, column)


def tokenize(source: str) -> list[Token]:
    scanner = Scanner(source)
    tokens = []
    while True:
        token = scanner.next_token()
        tokens.append(token)
        if token.kind == 'end':
            return tokens
