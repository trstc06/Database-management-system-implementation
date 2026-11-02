"""The relational algebra tree and its text printer. No parsing, no I/O.

Node kinds:
  queries     Relation, Select, Project, Rename, Distinct, Times, Join,
              Union, Intersect, Minus
  parameters  Attributes, Alias
  conditions  And, Or, Not, Eq, Ne, Lt, Le, Gt, Ge
  operands    Attr, Num, Str
  files       RelationFile, Definition, Row
"""

from dataclasses import dataclass, field
from .tokenizer import Token

COMPARISONS = ('Eq', 'Ne', 'Lt', 'Le', 'Gt', 'Ge')

ROLES = {'Select': ('condition: ', 'input: '),
         'Project': ('columns: ', 'input: '),
         'Rename': ('name: ', 'input: '),
         'Join': ('condition: ', 'left: ', 'right: ')}


@dataclass(frozen=True)
class Node:
    kind: str
    value: object = None
    children: tuple = ()
    token: Token = field(default=None, compare=False, repr=False)


def node_label(node: Node) -> str:
    if node.kind == 'Definition':
        name, columns = node.value
        return f'{node.kind}({name}, columns=[{", ".join(columns)}])'
    if node.value is None:
        return node.kind
    value = repr(node.value) if node.kind == 'Str' else str(node.value)
    return f'{node.kind}({value})'


def format_tree(tree: Node) -> str:
    """Print every node without evaluating it; iterative to handle long chains."""
    lines = []
    pending = [(tree, '', '', '')]
    while pending:
        node, prefix, connector, role = pending.pop()
        lines.append(prefix + connector + role + node_label(node))
        child_prefix = prefix + ('    ' if connector == '`-- ' else '|   ' if connector else '')
        for index in range(len(node.children) - 1, -1, -1):
            last = index == len(node.children) - 1
            child_role = ROLES[node.kind][index] if node.kind in ROLES else ''
            pending.append((node.children[index], child_prefix, '`-- ' if last else '|-- ', child_role))
    return '\n'.join(lines)
