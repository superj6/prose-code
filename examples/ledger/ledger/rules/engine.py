"""Apply an ordered list of rules; the first matching rule wins."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..models import LedgerError, Transaction
from .matchers import Matcher, keyword, regex


@dataclass(frozen=True)
class Rule:
    category: str
    matcher: Matcher
    source: str = ""     # the text the rule was loaded from, for error messages


def categorise(transactions: Iterable[Transaction], rules: list[Rule]) -> list[Transaction]:
    """Return copies of the transactions with the first matching rule's category applied."""
    out = []
    for tx in transactions:
        for rule in rules:
            if rule.matcher(tx):
                tx = tx.with_category(rule.category)
                break
        out.append(tx)
    return out


def load_rules(lines: Iterable[str]) -> list[Rule]:
    """Rules file: ``category: keyword one, keyword two`` or ``category: /regex/`` per line."""
    rules = []
    for n, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        category, sep, spec = line.partition(":")
        if not sep or not spec.strip():
            raise LedgerError(f"rules line {n}: expected 'category: spec'")
        spec = spec.strip()
        if spec.startswith("/") and spec.endswith("/") and len(spec) > 2:
            matcher = regex(spec[1:-1])
        else:
            matcher = keyword(*[w.strip() for w in spec.split(",") if w.strip()])
        rules.append(Rule(category.strip(), matcher, line))
    return rules
