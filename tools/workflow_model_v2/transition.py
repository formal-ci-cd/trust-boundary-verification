"""Small state transition IR and SMV backend, independent of YAML contracts."""
from __future__ import annotations

import hashlib
import json


def symbol(prefix, value):
    return prefix + '_' + hashlib.sha256(str(value).encode()).hexdigest()[:12]


def atom(value):
    return symbol('v', json.dumps(value, sort_keys=True))


def conjunction(*terms):
    return '(' + ' & '.join('(' + str(t) + ')' for t in terms) + ')' if terms else 'TRUE'


def disjunction(terms):
    terms = list(terms)
    return '(' + ' | '.join('(' + str(t) + ')' for t in terms) + ')' if terms else 'FALSE'


class TransitionSystem:
    def __init__(self):
        self.variables = {}
        self.frozen = {}
        self.inputs = {}
        self.rules = {}
        self.defines = {}
        self.properties = {}
        self.sources = {}
        self.constraints = []

    def var(self, name, values, initial, source=None):
        values = list(dict.fromkeys(values))
        # NuSMV rejects next() of a singleton enum folded into a constant.
        if len(values) == 1:
            values.append('unreachable_' + name)
        self.variables[name] = (values, initial)
        self.rules[name] = []
        if source:
            self.sources[name] = source
        return name

    def update(self, name, guard, value):
        self.rules[name].append((guard, value))

    def latch(self, name, guard):
        self.update(name, guard, 'TRUE')

    def render(self):
        lines = ['-- Finite bounded runs; nondeterministic outcomes are not observations.', 'MODULE main']
        for heading, decls in [('FROZENVAR', self.frozen), ('IVAR', self.inputs)]:
            if decls:
                lines.append(heading)
                for name, domain in decls.items():
                    lines.append(f'  {name} : ' + ('boolean' if domain == ['FALSE', 'TRUE'] else '{ ' + ', '.join(domain) + ' }') + ';')
        lines.append('VAR')
        for name, (domain, _) in self.variables.items():
            lines.append(f'  {name} : ' + ('boolean' if domain == ['FALSE', 'TRUE'] else '{ ' + ', '.join(domain) + ' }') + ';')
        if self.defines:
            lines.append('DEFINE')
            lines.extend(f'  {n} := {expr};' for n, expr in self.defines.items())
        lines.append('ASSIGN')
        for name, (_, initial) in self.variables.items():
            lines.extend([f'  init({name}) := {initial};', f'  next({name}) := case'])
            lines.extend(f'    {guard} : {value};' for guard, value in self.rules[name])
            lines.extend([f'    TRUE : {name};', '  esac;'])
        lines.extend('TRANS ' + constraint for constraint in self.constraints)
        lines.extend(f'CTLSPEC NAME {name} := {expr};' for name, expr in self.properties.items())
        return '\n'.join(lines) + '\n'
