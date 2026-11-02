"""Handwritten tokenizer for relational algebra text and relation files.

No regular expressions. Tokens keep their offset, line and column so every
later error can point at the source. Condition strings inside physical and
logical plans use the same scanner, so it accepts both `!=` and the SQL
spelling `<>`. Numbers are integers only, because INT is the only numeric
type in this project.
"""

from dataclasses import dataclass
from ..common.errors import LexicalError


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    value: str
    offset: int
    line: int
    column: int


TWO_CHARACTER_COMPARISONS = ('>=', '<=', '!=', '<>')


def is_digit(char: str) -> bool:
    return '0' <= char <= '9'


def is_name_start(char: str) -> bool:
    return 'a' <= char <= 'z' or 'A' <= char <= 'Z' or char == '_'


def is_number(text: str) -> bool:
    """Check a complete relation field against the integer rule."""
    start = 1 if text.startswith('-') else 0
    return len(text) > start and all(is_digit(char) for char in text[start:])


def is_decimal(text: str) -> bool:
    """A field such as -30.5, which Bonus 1 accepted and INT cannot hold."""
    whole, dot, fraction = text.partition('.')
    return bool(dot) and is_number(whole) and fraction != '' and all(is_digit(c) for c in fraction)


def tokenize(source: str, *, relations: bool = False) -> list[Token]:
    """Return tokens with 0-based offsets and 1-based lines/columns.

    Numeric values keep their spelling so evaluation chooses the
    representation later. NAME tokens include contextual operator words.
    In relations mode, braces switch between headers and row values, and
    NEWLINE tokens preserve nonblank line boundaries.
    """
    tokens = []
    i, line, column = 0, 1, 1
    line_has_content = False
    in_body = False
    punctuation = {
        '[': 'LBRACKET', ']': 'RBRACKET', '(': 'LPAREN', ')': 'RPAREN',
        ',': 'COMMA', '.': 'DOT',
    }
    if relations:
        punctuation.update({'{': 'LBRACE', '}': 'RBRACE'})
    while i < len(source):
        char = source[i]
        if char in ' \t':
            i += 1
            column += 1
            continue
        if char in '\r\n':
            start = i
            if char == '\r' and i + 1 < len(source) and source[i + 1] == '\n':
                i += 1
            i += 1
            if relations and line_has_content:
                tokens.append(Token('NEWLINE', source[start:i], '\n', start, line, column))
            line += 1
            column = 1
            line_has_content = False
            continue
        if not line_has_content and source.startswith('//', i):
            while i < len(source) and source[i] not in '\r\n':
                i += 1
                column += 1
            continue

        start, start_line, start_column = i, line, column
        line_has_content = True
        if relations and in_body and char not in ",}'":
            # Bare fields can contain punctuation, e.g. E-1 or a.b.
            while i < len(source) and source[i] not in ',}\r\n':
                i += 1
            value = source[start:i].rstrip(' \t')
            for index, character in enumerate(value):
                if character.isspace() or character in "(){}'":
                    raise LexicalError('quote this text value', start + index, start_line, start_column + index)
            if value.startswith('//'):
                raise LexicalError('quote values starting with //', start, start_line, start_column)
            if is_decimal(value):
                raise LexicalError('only integer numbers are supported', start, start_line, start_column)
            # Leave padding for the normal whitespace handler.
            i = start + len(value)
            kind = 'NUMBER' if is_number(value) else 'STRING'
        elif is_name_start(char):
            i += 1
            while i < len(source) and (is_name_start(source[i]) or is_digit(source[i])):
                i += 1
            kind, value = 'NAME', source[start:i]
        elif is_digit(char) or (char == '-' and i + 1 < len(source) and is_digit(source[i + 1])):
            if char == '-':
                i += 1
            while i < len(source) and is_digit(source[i]):
                i += 1
            if i < len(source) and source[i] == '.' and i + 1 < len(source) and is_digit(source[i + 1]):
                raise LexicalError('only integer numbers are supported', start, start_line, start_column)
            kind, value = 'NUMBER', source[start:i]
        elif char == "'":
            i += 1
            characters = []
            while True:
                if i == len(source) or source[i] in '\r\n':
                    raise LexicalError('unterminated string', start, start_line, start_column)
                if source[i] == "'":
                    if i + 1 < len(source) and source[i + 1] == "'":
                        characters.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                characters.append(source[i])
                i += 1
            kind, value = 'STRING', ''.join(characters)
            if relations and in_body:
                following = i
                while following < len(source) and source[following] in ' \t':
                    following += 1
                if following < len(source) and source[following] not in ',}\r\n':
                    raise LexicalError('expected a delimiter after quoted value', following, line, column + following - start)
        elif source[i:i + 2] in TWO_CHARACTER_COMPARISONS:
            i += 2
            kind, value = 'COMPARE', source[start:i]
        elif char in '=<>':
            i += 1
            kind, value = 'COMPARE', char
        elif char in punctuation:
            i += 1
            kind, value = punctuation[char], char
            if relations and char in '{}':
                in_body = char == '{'
        else:
            raise LexicalError(f'unexpected character {char!r}', start, start_line, start_column)
        column += i - start
        tokens.append(Token(kind, source[start:i], value, start, start_line, start_column))
    tokens.append(Token('EOF', '', '', i, line, column))
    return tokens
