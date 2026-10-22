"""Closed GitHub expression AST and finite-value lowering (no hazard decisions)."""
from __future__ import annotations

import itertools
import json
import math
import re
from dataclasses import dataclass


class UnsupportedExpression(ValueError):
    pass


@dataclass(frozen=True)
class Node:
    kind: str
    value: object = None
    args: tuple = ()


TOKEN = re.compile(r"\s+|'(?:''|[^'])*'|(?:0[xX][0-9a-fA-F]+|-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|[A-Za-z_][\w.-]*|==|!=|<=|>=|&&|\|\||[!<>()\[\],.]")


def parse(source):
    if not isinstance(source, str):
        return Node('literal', source)
    text = source.strip()
    if text.startswith('${{') and text.endswith('}}'):
        text = text[3:-2].strip()
    tokens, end = [], 0
    for match in TOKEN.finditer(text):
        if match.start() != end:
            raise UnsupportedExpression('unrecognized expression token at ' + text[end:])
        end = match.end()
        if not match.group().isspace():
            tokens.append(match.group())
    if end != len(text):
        raise UnsupportedExpression('unrecognized expression token at ' + text[end:])
    tokens.append('<end>')
    pos = 0

    def take(expected=None):
        nonlocal pos
        token = tokens[pos]
        if expected is not None and token != expected:
            raise UnsupportedExpression(f'expected {expected!r}, got {token!r}')
        pos += 1
        return token

    def primary():
        token = take()
        if token == '!':
            result = Node('!', args=(primary(),))
        elif token == '(':
            result = binary(0)
            take(')')
        elif token.startswith("'"):
            result = Node('literal', token[1:-1].replace("''", "'"))
        elif token in {'true', 'false', 'null'}:
            result = Node('literal', {'true': True, 'false': False, 'null': None}[token])
        elif re.match(r'^-?\d|^\.\d', token):
            try:
                result = Node('literal', int(token, 16) if token.lower().startswith('0x') else json.loads(token))
            except ValueError as exc:
                raise UnsupportedExpression(str(exc)) from exc
        elif re.fullmatch(r'[A-Za-z_][\w.-]*', token):
            if tokens[pos] == '(':
                take('(')
                args = []
                if tokens[pos] != ')':
                    args.append(binary(0))
                    while tokens[pos] == ',':
                        take(',')
                        args.append(binary(0))
                take(')')
                result = Node('call', token.lower(), tuple(args))
            else:
                result = Node('ref', token)
        else:
            raise UnsupportedExpression(f'unexpected {token!r}')
        while tokens[pos] == '[':
            take('[')
            index = binary(0)
            take(']')
            if result.kind == 'ref' and index.kind == 'literal' and isinstance(index.value, str):
                result = Node('ref', str(result.value) + '.' + index.value)
            else:
                result = Node('index', args=(result, index))
        return result

    precedence = {'||': 1, '&&': 2, '==': 3, '!=': 3, '<': 4, '>': 4, '<=': 4, '>=': 4}

    def binary(minimum):
        left = primary()
        while tokens[pos] in precedence and precedence[tokens[pos]] >= minimum:
            op = take()
            left = Node(op, args=(left, binary(precedence[op] + 1)))
        return left

    try:
        result = binary(0)
        if tokens[pos] != '<end>':
            raise UnsupportedExpression('trailing expression: ' + tokens[pos])
        return result
    except IndexError as exc:
        raise UnsupportedExpression('incomplete expression') from exc


def truth(value):
    return not (value is None or value is False or value == '' or (type(value) in {int, float} and value == 0))


def number(value):
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if type(value) in {int, float}:
        return value
    if isinstance(value, str):
        if not value.strip():
            return 0
        try:
            parsed = json.loads(value)
            return parsed if type(parsed) in {int, float} else math.nan
        except ValueError:
            return math.nan
    return math.nan


def string(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if type(value) in {int, float}:
        return str(value)
    raise UnsupportedExpression('array/object cannot be converted to string')


def equal(a, b):
    if type(a) == type(b):
        if isinstance(a, str):
            return a.lower() == b.lower()
        if isinstance(a, (dict, list)):
            raise UnsupportedExpression('object/array instance identity is not abstracted')
        return a == b
    return number(a) == number(b)


def evaluate(node, references, statuses=None):
    statuses = statuses or {}
    if node.kind == 'literal':
        return node.value
    if node.kind == 'ref':
        return references[str(node.value)]
    if node.kind == '&&':
        left = evaluate(node.args[0], references, statuses)
        return evaluate(node.args[1], references, statuses) if truth(left) else left
    if node.kind == '||':
        left = evaluate(node.args[0], references, statuses)
        return left if truth(left) else evaluate(node.args[1], references, statuses)
    args = [evaluate(arg, references, statuses) for arg in node.args]
    if node.kind == '!':
        return not truth(args[0])
    if node.kind in {'==', '!='}:
        result = equal(*args)
        return result if node.kind == '==' else not result
    if node.kind in {'<', '>', '<=', '>='}:
        a, b = args
        if isinstance(a, str) and isinstance(b, str):
            a, b = a.lower(), b.lower()
        else:
            a, b = number(a), number(b)
        return {'<': lambda: a < b, '>': lambda: a > b, '<=': lambda: a <= b, '>=': lambda: a >= b}[node.kind]()
    if node.kind == 'index':
        try:
            return args[0][args[1]]
        except (KeyError, IndexError, TypeError):
            return ''
    if node.kind != 'call':
        raise UnsupportedExpression('unsupported AST node ' + node.kind)
    name = node.value
    arities = {'always': 0, 'success': 0, 'failure': 0, 'cancelled': 0,
               'contains': 2, 'startswith': 2, 'endswith': 2, 'fromjson': 1, 'tojson': 1}
    if name in arities and len(args) != arities[name]:
        raise UnsupportedExpression(f'{name}: invalid argument count')
    if name in {'always', 'success', 'failure', 'cancelled'}:
        return True if name == 'always' else statuses[name]
    if name == 'contains':
        return any(equal(x, args[1]) for x in args[0]) if isinstance(args[0], list) else string(args[1]).lower() in string(args[0]).lower()
    if name == 'startswith':
        return string(args[0]).lower().startswith(string(args[1]).lower())
    if name == 'endswith':
        return string(args[0]).lower().endswith(string(args[1]).lower())
    if name == 'fromjson':
        try:
            return json.loads(string(args[0]))
        except ValueError as exc:
            raise UnsupportedExpression('fromJSON input is invalid JSON') from exc
    if name == 'tojson':
        return json.dumps(args[0], ensure_ascii=False, indent=2)
    if name == 'format' and args:
        try:
            return string(args[0]).format(*(string(x) for x in args[1:]))
        except (KeyError, IndexError, ValueError) as exc:
            raise UnsupportedExpression('format pattern outside numbered placeholder subset') from exc
    raise UnsupportedExpression('unsupported function ' + str(name))


def walk(node):
    yield node
    for arg in node.args:
        yield from walk(arg)


def key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def lower(node, resolve, statuses, limit=8192):
    """Return (concrete abstract value, SMV predicate) alternatives.

    Repeated references share a single valuation. The resolver supplies an
    explicit finite domain; absence of a complete domain is diagnosed upstream.
    """
    refs = sorted({str(n.value) for n in walk(node) if n.kind == 'ref'})
    calls = sorted({str(n.value) for n in walk(node) if n.kind == 'call' and n.value in {'success', 'failure', 'cancelled'}})
    dimensions = [resolve(ref) for ref in refs] + [[(True, statuses[c]), (False, f'!({statuses[c]})')] for c in calls]
    if math.prod(map(len, dimensions)) > limit:
        raise UnsupportedExpression('finite expression product exceeds ' + str(limit))
    grouped = {}
    for assignment in itertools.product(*dimensions):
        values = dict(zip(refs, (a[0] for a in assignment[:len(refs)])))
        status = dict(zip(calls, (a[0] for a in assignment[len(refs):])))
        value = evaluate(node, values, status)
        pred = ' & '.join(f'({a[1]})' for a in assignment) or 'TRUE'
        token = key(value)
        if token not in grouped:
            grouped[token] = [value, []]
        grouped[token][1].append(pred)
    return [(value, '(' + ' | '.join(preds) + ')') for value, preds in grouped.values()]
