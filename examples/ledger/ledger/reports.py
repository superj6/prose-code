"""Aggregations over transactions. All results are plain dicts so they serialise easily."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .models import Transaction


def monthly_totals(transactions: Iterable[Transaction]) -> dict[str, dict[str, int]]:
    """{'2026-08': {'in': cents, 'out': cents, 'net': cents}, ...} sorted by month."""
    months: dict[str, dict[str, int]] = defaultdict(lambda: {"in": 0, "out": 0, "net": 0})
    for tx in transactions:
        key = tx.when.strftime("%Y-%m")
        bucket = months[key]
        if tx.is_expense:
            bucket["out"] += -tx.amount_cents
        else:
            bucket["in"] += tx.amount_cents
        bucket["net"] += tx.amount_cents
    return dict(sorted(months.items()))


def top_categories(transactions: Iterable[Transaction], limit: int = 5) -> list[tuple[str, int]]:
    """Expense categories by total spend (cents, positive), largest first."""
    totals: dict[str, int] = defaultdict(int)
    for tx in transactions:
        if tx.is_expense:
            totals[tx.category] += -tx.amount_cents
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:limit]


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"
