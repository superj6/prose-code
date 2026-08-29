"""Core data types. Amounts are integers in cents to avoid float drift."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


class LedgerError(ValueError):
    """Raised for malformed or inconsistent ledger data."""


@dataclass(frozen=True)
class Transaction:
    when: date
    amount_cents: int          # negative = money out, positive = money in
    payee: str
    category: str = "uncategorised"
    note: str = ""

    def __post_init__(self) -> None:
        if not self.payee.strip():
            raise LedgerError("transaction needs a payee")
        if self.amount_cents == 0:
            raise LedgerError("transaction amount cannot be zero")

    @property
    def is_expense(self) -> bool:
        return self.amount_cents < 0

    def with_category(self, category: str) -> "Transaction":
        return Transaction(self.when, self.amount_cents, self.payee, category, self.note)


@dataclass
class Account:
    name: str
    opening_cents: int = 0
    transactions: list[Transaction] = field(default_factory=list)

    def add(self, tx: Transaction) -> None:
        self.transactions.append(tx)
        self.transactions.sort(key=lambda t: t.when)

    @property
    def balance_cents(self) -> int:
        return self.opening_cents + sum(t.amount_cents for t in self.transactions)

    def between(self, start: date, end: date) -> list[Transaction]:
        """Transactions with start <= when < end."""
        return [t for t in self.transactions if start <= t.when < end]
