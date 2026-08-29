"""Matchers test a transaction's payee (and note). Each factory returns a callable."""
from __future__ import annotations

import re
from collections.abc import Callable

from ..models import Transaction

Matcher = Callable[[Transaction], bool]


def keyword(*words: str) -> Matcher:
    """True when any word appears in the payee or note, case-insensitively."""
    needles = [w.lower() for w in words]

    def match(tx: Transaction) -> bool:
        hay = f"{tx.payee} {tx.note}".lower()
        return any(n in hay for n in needles)

    return match


def regex(pattern: str) -> Matcher:
    """True when the pattern is found in the payee (case-insensitive)."""
    compiled = re.compile(pattern, re.IGNORECASE)

    def match(tx: Transaction) -> bool:
        return compiled.search(tx.payee) is not None

    return match


def amount_below(cents: int) -> Matcher:
    """True for expenses smaller than the given size (in cents, positive)."""

    def match(tx: Transaction) -> bool:
        return tx.is_expense and -tx.amount_cents < cents

    return match
