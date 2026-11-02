"""Recursive descent parser for relational algebra, conditions and relation files.

`parse` reads a whole query (or a relation file), `parse_condition` reads a
condition string on its own. The condition parser is the piece Modules 2, 3
and 4 reuse: plan JSON carries conditions such as "R.a > 500 and R.b = 'cs'".
Condition keywords (and, or, not) ignore case so SQL's AND/OR/NOT also read;
query operators keep Bonus 1's lowercase spelling.
"""

from ..common.errors import ParseError
from .tokenizer import Token, tokenize
from .tree import Node

COMPARISON_KINDS = {'=': 'Eq', '!=': 'Ne', '<>': 'Ne', '<': 'Lt', '<=': 'Le', '>': 'Gt', '>=': 'Ge'}
UNARY_OPERATIONS = ('select', 'project', 'rename')


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def peek(self) -> Token:
        return self.tokens[min(self.index + 1, len(self.tokens) - 1)]

    def take(self) -> Token:
        token = self.current
        if token.kind != 'EOF':
            self.index += 1
        return token

    def accept(self, kind: str, value: str | None = None) -> Token | None:
        if self.current.kind == kind and (value is None or self.current.value == value):
            return self.take()
        return None

    def expect(self, kind: str, description: str, value: str | None = None) -> Token:
        token = self.accept(kind, value)
        if token is None:
            found = 'end of input' if self.current.kind == 'EOF' else repr(self.current.text)
            raise ParseError(f'expected {description}; found {found}', self.current)
        return token

    def at_word(self, words, *, fold: bool = False) -> bool:
        if self.current.kind != 'NAME':
            return False
        word = self.current.value.lower() if fold else self.current.value
        return word in words

    def binary_sequence(self, operand, operators: dict, *, fold: bool = False) -> Node:
        left = operand()
        while self.at_word(operators, fold=fold):
            operator = self.take()
            word = operator.value.lower() if fold else operator.value
            right = operand()
            left = Node(operators[word], children=(left, right), token=operator)
        return left

    # Queries -------------------------------------------------------------

    def expression(self) -> Node:
        return self.binary_sequence(self.intersection, {'union': 'Union', 'minus': 'Minus'})

    def intersection(self) -> Node:
        return self.binary_sequence(self.product, {'intersect': 'Intersect'})

    def product(self) -> Node:
        left = self.primary()
        while self.at_word(('times', 'join')):
            operator = self.take()
            if operator.value == 'join':
                self.expect('LBRACKET', "'[' after join")
                condition = self.condition()
                self.expect('RBRACKET', "']' after join condition")
                right = self.primary()
                left = Node('Join', children=(condition, left, right), token=operator)
            else:
                left = Node('Times', children=(left, self.primary()), token=operator)
        return left

    def primary(self) -> Node:
        if self.accept('LPAREN'):
            result = self.expression()
            self.expect('RPAREN', "closing ')' for expression")
            return result
        name = self.expect('NAME', 'a relation name or an expression')
        if name.value == 'distinct' and self.current.kind == 'LPAREN':
            self.take()
            child = self.expression()
            self.expect('RPAREN', "closing ')' after distinct input")
            return Node('Distinct', children=(child,), token=name)
        if name.value not in UNARY_OPERATIONS or self.current.kind != 'LBRACKET':
            return Node('Relation', name.value, token=name)
        self.take()
        if name.value == 'select':
            parameter = self.condition()
        elif name.value == 'project':
            attributes = [self.attribute()]
            while self.accept('COMMA'):
                attributes.append(self.attribute())
            parameter = Node('Attributes', children=tuple(attributes))
        else:
            alias = self.expect('NAME', 'a new relation name')
            parameter = Node('Alias', alias.value, token=alias)
        self.expect('RBRACKET', "closing ']' after parameter")
        self.expect('LPAREN', "'(' before input expression")
        child = self.expression()
        self.expect('RPAREN', "closing ')' after input expression")
        return Node(name.value.capitalize(), children=(parameter, child), token=name)

    # Conditions ----------------------------------------------------------

    def condition(self) -> Node:
        return self.binary_sequence(self.conjunction, {'or': 'Or'}, fold=True)

    def conjunction(self) -> Node:
        return self.binary_sequence(self.negation, {'and': 'And'}, fold=True)

    def negation(self) -> Node:
        # `not` followed by a comparison or a dot is a column named not.
        if self.at_word(('not',), fold=True) and self.peek().kind not in ('COMPARE', 'DOT'):
            operator = self.take()
            return Node('Not', children=(self.negation(),), token=operator)
        if self.accept('LPAREN'):
            result = self.condition()
            self.expect('RPAREN', "closing ')' in condition")
            return result
        left = self.operand()
        operator = self.expect('COMPARE', 'a comparison operator')
        right = self.operand()
        return Node(COMPARISON_KINDS[operator.value], children=(left, right), token=operator)

    def attribute(self) -> Node:
        name = self.expect('NAME', 'an attribute name (the list cannot be empty)')
        value = name.value
        if self.accept('DOT'):
            value += '.' + self.expect('NAME', 'an attribute name after the dot').value
        return Node('Attr', value, token=name)

    def literal(self) -> Node:
        token = self.current
        if token.kind not in ('NUMBER', 'STRING'):
            raise ParseError('expected a number or string value', token)
        self.take()
        return Node('Num' if token.kind == 'NUMBER' else 'Str', token.value, token=token)

    def operand(self) -> Node:
        if self.current.kind == 'NAME':
            return self.attribute()
        return self.literal()

    # Relation files ------------------------------------------------------

    def relation_file(self) -> Node:
        definitions = []
        while self.accept('NEWLINE'):
            pass
        while self.current.kind != 'EOF':
            definitions.append(self.definition())
        return Node('RelationFile', children=tuple(definitions))

    def definition(self) -> Node:
        name = self.expect('NAME', 'a relation name')
        self.expect('LPAREN', "'(' before column names")
        columns = [self.expect('NAME', 'a column name').value]
        while self.accept('COMMA'):
            columns.append(self.expect('NAME', 'a column name').value)
        self.expect('RPAREN', "')' after column names")
        self.expect('COMPARE', "'=' before table body", '=')
        self.expect('LBRACE', "'{' before table body")
        self.expect('NEWLINE', 'a line break before table rows')
        rows = []
        while self.current.kind not in ('RBRACE', 'EOF'):
            values = [self.literal()]
            while self.accept('COMMA'):
                values.append(self.literal())
            self.expect('NEWLINE', 'a line break after the row')
            rows.append(Node('Row', children=tuple(values), token=values[0].token))
        self.expect('RBRACE', "closing '}' for table")
        # The closing brace must occupy its own line.
        if self.current.kind not in ('NEWLINE', 'EOF'):
            raise ParseError("expected a line break after '}'", self.current)
        while self.accept('NEWLINE'):
            pass
        return Node('Definition', (name.value, tuple(columns)), tuple(rows), name)


def run(parser: Parser, rule) -> Node:
    try:
        result = rule()
        parser.expect('EOF', 'end of input')
        return result
    except RecursionError:
        raise ParseError('input nesting is too deep', parser.current) from None


def parse(source: str, *, relations: bool = False) -> Node:
    parser = Parser(tokenize(source, relations=relations))
    return run(parser, parser.relation_file if relations else parser.expression)


def parse_condition(source: str) -> Node:
    """Parse a condition string such as "R.a > 500 and R.b = 'cs'"."""
    parser = Parser(tokenize(source))
    return run(parser, parser.condition)


def parse_attribute(source: str) -> Node:
    """Parse one column reference, `c` or `t.c`."""
    parser = Parser(tokenize(source))
    return run(parser, parser.attribute)
