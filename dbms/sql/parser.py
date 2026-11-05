"""Recursive descent parser for the SQL subset. See docs/GRAMMAR_SQL.md.

One method per grammar rule. Precedence and associativity come from the
shape of the rules: OR is made of ANDs, AND is made of NOTs, and set
operators are a left to right loop over select blocks.
"""

from ..common.errors import DbError
from ..common.schema import INT, VARCHAR
from . import ast
from .lexer import Token, tokenize

AGGREGATES = ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX')
COMPARISONS = ('=', '<>', '<', '<=', '>', '>=')
SET_OPERATORS = ('UNION', 'INTERSECT', 'EXCEPT')


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    # Token helpers ------------------------------------------------------

    @property
    def current(self) -> Token:
        return self.tokens[self.i]

    def pos(self) -> ast.Pos:
        return ast.Pos(self.current.line, self.current.column)

    def at(self, kind: str, *values: str) -> bool:
        token = self.current
        return token.kind == kind and (not values or token.value in values)

    def at_keyword(self, *words: str) -> bool:
        return self.at('keyword', *words)

    def at_symbol(self, *symbols: str) -> bool:
        return self.at('symbol', *symbols)

    def take(self) -> Token:
        token = self.current
        if token.kind != 'end':
            self.i += 1
        return token

    def error(self, expected: str) -> DbError:
        token = self.current
        return DbError('syntax', f'expected {expected}, found {token.describe()}', token.line, token.column)

    def expect_keyword(self, word: str) -> Token:
        if not self.at_keyword(word):
            raise self.error(word)
        return self.take()

    def expect_symbol(self, symbol: str) -> Token:
        if not self.at_symbol(symbol):
            raise self.error(f"'{symbol}'")
        return self.take()

    def identifier(self, what: str) -> str:
        if not self.at('ident'):
            if self.at('keyword'):
                raise self.error(f'{what} (a keyword such as {self.current.value} cannot be a name)')
            raise self.error(what)
        return self.take().value

    def integer(self, what: str) -> int:
        if not self.at('int'):
            raise self.error(what)
        return int(self.take().value)

    # Statements ---------------------------------------------------------

    def statements(self) -> list:
        result = []
        while True:
            while self.at_symbol(';'):
                self.take()
            if self.at('end'):
                return result
            result.append(self.statement())
            if not self.at('end'):
                self.expect_symbol(';')

    def statement(self):
        if self.at_keyword('CREATE'):
            return self.create()
        if self.at_keyword('INSERT'):
            return self.insert()
        if self.at_keyword('SELECT') or self.at_symbol('('):
            return self.query()
        raise self.error('SELECT, CREATE TABLE or INSERT INTO')

    def create(self) -> ast.CreateTable:
        pos = self.pos()
        self.expect_keyword('CREATE')
        self.expect_keyword('TABLE')
        table = self.identifier('a table name')
        self.expect_symbol('(')
        columns = [self.column_def()]
        while self.at_symbol(','):
            self.take()
            columns.append(self.column_def())
        self.expect_symbol(')')
        return ast.CreateTable(table, tuple(columns), pos)

    def column_def(self) -> ast.ColumnDef:
        pos = self.pos()
        name = self.identifier('a column name')
        if self.at_keyword('INT'):
            self.take()
            return ast.ColumnDef(name, INT, None, pos)
        if not self.at_keyword('VARCHAR'):
            raise self.error('INT or VARCHAR(n)')
        self.take()
        self.expect_symbol('(')
        length = self.integer('a VARCHAR length')
        self.expect_symbol(')')
        return ast.ColumnDef(name, VARCHAR, length, pos)

    def insert(self) -> ast.Insert:
        pos = self.pos()
        self.expect_keyword('INSERT')
        self.expect_keyword('INTO')
        table = self.identifier('a table name')
        self.expect_keyword('VALUES')
        self.expect_symbol('(')
        values = [self.literal()]
        while self.at_symbol(','):
            self.take()
            values.append(self.literal())
        self.expect_symbol(')')
        return ast.Insert(table, tuple(values), pos)

    def literal(self) -> ast.Literal:
        pos = self.pos()
        if self.at('int'):
            return ast.Literal(int(self.take().value), pos)
        if self.at('string'):
            return ast.Literal(self.take().value, pos)
        raise self.error('a number or a string')

    # Queries ------------------------------------------------------------

    def query(self):
        """query ::= select_block { setop select_block }, grouped to the left."""
        left = self.select_block()
        while self.at_keyword(*SET_OPERATORS):
            pos = self.pos()
            op = self.take().value.lower()
            if op == 'union' and self.at_keyword('ALL'):
                self.take()
                op = 'union_all'
            left = ast.SetOperation(op, left, self.select_block(), pos)
        return left

    def select_block(self):
        if self.at_symbol('('):
            self.take()
            inner = self.query()
            self.expect_symbol(')')
            return inner
        pos = self.pos()
        self.expect_keyword('SELECT')
        distinct = self.at_keyword('DISTINCT')
        if distinct:
            self.take()
        items = self.select_list()
        self.expect_keyword('FROM')
        source = self.from_list()
        where = self.clause('WHERE', self.condition)
        group_by, having = (), None
        if self.at_keyword('GROUP'):
            self.take()
            self.expect_keyword('BY')
            group_by = self.comma_list(self.column)
            having = self.clause('HAVING', self.condition)
        order_by = ()
        if self.at_keyword('ORDER'):
            self.take()
            self.expect_keyword('BY')
            order_by = self.comma_list(self.order_item)
        limit = None
        if self.at_keyword('LIMIT'):
            self.take()
            limit = self.integer('a row count after LIMIT')
            if limit < 0:
                previous = self.tokens[self.i - 1]
                raise DbError('syntax', 'LIMIT needs a count of 0 or more', previous.line, previous.column)
        return ast.SelectBlock(distinct, items, source, where, group_by, having, order_by, limit, pos)

    def clause(self, keyword: str, rule):
        if not self.at_keyword(keyword):
            return None
        self.take()
        return rule()

    def comma_list(self, rule) -> tuple:
        items = [rule()]
        while self.at_symbol(','):
            self.take()
            items.append(rule())
        return tuple(items)

    def select_list(self):
        if self.at_symbol('*'):
            pos = self.pos()
            self.take()
            return ast.Star(pos)
        return self.comma_list(self.select_item)

    def select_item(self) -> ast.SelectItem:
        pos = self.pos()
        expr = self.aggregate() if self.at_keyword(*AGGREGATES) else self.column()
        alias = None
        if self.at_keyword('AS'):
            self.take()
            alias = self.identifier('a name after AS')
        return ast.SelectItem(expr, alias, pos)

    def aggregate(self) -> ast.Aggregate:
        pos = self.pos()
        function = self.take().value.lower()
        self.expect_symbol('(')
        if self.at_symbol('*'):
            self.take()
            argument = None
        else:
            argument = self.column()
        self.expect_symbol(')')
        return ast.Aggregate(function, argument, pos)

    def column(self) -> ast.ColumnRef:
        pos = self.pos()
        first = self.identifier('a column name')
        if self.at_symbol('.'):
            self.take()
            return ast.ColumnRef(first, self.identifier('a column name after the dot'), pos)
        return ast.ColumnRef(None, first, pos)

    def order_item(self) -> ast.OrderItem:
        column = self.column()
        descending = False
        if self.at_keyword('ASC', 'DESC'):
            descending = self.take().value == 'DESC'
        return ast.OrderItem(column, descending)

    def from_list(self) -> ast.FromClause:
        refs = [self.table_ref()]
        if self.at_keyword('JOIN'):
            conditions = []
            while self.at_keyword('JOIN'):
                self.take()
                refs.append(self.table_ref())
                self.expect_keyword('ON')
                conditions.append(self.condition())
            return ast.FromClause(tuple(refs), tuple(conditions), True)
        while self.at_symbol(','):
            self.take()
            refs.append(self.table_ref())
        if self.at_keyword('JOIN'):
            raise self.error("',' (a FROM list uses commas or JOIN ... ON, not both)")
        return ast.FromClause(tuple(refs), (), False)

    def table_ref(self):
        pos = self.pos()
        if self.at_symbol('('):
            self.take()
            query = self.query()
            self.expect_symbol(')')
            if self.at_keyword('AS'):
                self.take()
            if not self.at('ident'):
                raise self.error('an alias for the subquery (a subquery in FROM needs one)')
            return ast.SubqueryRef(query, self.take().value, pos)
        name = self.identifier('a table name')
        alias = None
        if self.at_keyword('AS'):
            self.take()
            alias = self.identifier('an alias after AS')
        elif self.at('ident'):
            alias = self.take().value
        return ast.TableRef(name, alias, pos)

    # Conditions: OR of ANDs of NOTs of comparisons ------------------------

    def condition(self):
        pos = self.pos()
        left = self.conjunction()
        while self.at_keyword('OR'):
            self.take()
            left = ast.Logical('or', (left, self.conjunction()), pos)
        return left

    def conjunction(self):
        pos = self.pos()
        left = self.negation()
        while self.at_keyword('AND'):
            self.take()
            left = ast.Logical('and', (left, self.negation()), pos)
        return left

    def negation(self):
        if self.at_keyword('NOT'):
            pos = self.pos()
            self.take()
            return ast.Logical('not', (self.negation(),), pos)
        if self.at_symbol('('):
            self.take()
            inner = self.condition()
            self.expect_symbol(')')
            return inner
        return self.comparison()

    def comparison(self) -> ast.Comparison:
        pos = self.pos()
        left = self.operand()
        if not self.at_symbol(*COMPARISONS):
            raise self.error('a comparison operator (=, <>, <, <=, >, >=)')
        op = self.take().value
        return ast.Comparison(op, left, self.operand(), pos)

    def operand(self):
        if self.at_keyword(*AGGREGATES):
            return self.aggregate()
        if self.at('int') or self.at('string'):
            return self.literal()
        if self.at('ident'):
            return self.column()
        raise self.error('a column, a number or a string')


def parse(source: str) -> list:
    """Every statement in `source`, in order."""
    return Parser(tokenize(source)).statements()


def parse_query(source: str):
    statements = parse(source)
    if len(statements) != 1:
        raise DbError('syntax', f'expected one statement, found {len(statements)}', 1, 1)
    return statements[0]
