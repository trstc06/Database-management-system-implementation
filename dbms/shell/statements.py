"""Split shell input into statements at semicolons.

A semicolon inside a quoted string or after -- on a line does not end a
statement. Hand-written, like the SQL lexer, so no regular expressions.
"""


def split_statements(text: str) -> tuple[list[str], str]:
    """Return the complete statements in `text` and the unfinished rest."""
    statements, start = [], 0
    i, in_string, in_comment = 0, False, False
    while i < len(text):
        char = text[i]
        if in_comment:
            in_comment = char != '\n'
        elif in_string:
            if char == "'" and text[i + 1:i + 2] == "'":
                i += 1          # '' is a quote inside the string
            elif char == "'":
                in_string = False
        elif char == "'":
            in_string = True
        elif char == '-' and text[i + 1:i + 2] == '-':
            in_comment = True
        elif char == ';':
            statements.append(padding(text, start) + text[start:i])
            start = i + 1
        i += 1
    statements = [s for s in statements if not is_blank(s)]
    return statements, padding(text, start) + text[start:]


def padding(text: str, start: int) -> str:
    """Blank lines and spaces that put a statement at its real line and column,
    so error positions count from the start of the whole input."""
    lines_before = text.count('\n', 0, start)
    line_start = text.rfind('\n', 0, start) + 1
    return '\n' * lines_before + ' ' * (start - line_start)


def is_blank(text: str) -> bool:
    """True if `text` holds only spaces and -- comments."""
    for line in text.splitlines():
        code = line.split('--', 1)[0]
        if code.strip():
            return False
    return True


def first_word(text: str) -> tuple[str, int]:
    """The first word (upper case) after spaces and comments, and where the text after it starts."""
    i = 0
    while i < len(text):
        if text[i] in ' \t\r\n':
            i += 1
        elif text.startswith('--', i):
            while i < len(text) and text[i] != '\n':
                i += 1
        else:
            break
    end = i
    while end < len(text) and (text[end].isalnum() or text[end] == '_'):
        end += 1
    return text[i:end].upper(), end


def blank_out(text: str, end: int) -> str:
    """Replace text[:end] with spaces, keeping newlines, so error columns still match the input."""
    return ''.join(c if c == '\n' else ' ' for c in text[:end]) + text[end:]
