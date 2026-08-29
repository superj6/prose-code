"""Parse the ledger text format: one transaction per line.

    2026-08-01  -12.50  Corner Cafe        # flat white
    2026-08-02  1500.00 ACME Payroll

Fields are separated by two or more spaces; an optional ``# note`` ends the line.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from .models import LedgerError, Transaction

_SPLIT = re.compile(r"\s{2,}")


def parse_amount(text: str) -> int:
    """'-12.50' -> -1250. Accepts a leading sign and up to two decimals."""
    m = re.fullmatch(r"([+-]?)(\d+)(?:\.(\d{1,2}))?", text.strip())
    if not m:
        raise LedgerError(f"bad amount {text!r}")
    sign, whole, frac = m.groups()
    cents = int(whole) * 100 + int((frac or "0").ljust(2, "0"))
    return -cents if sign == "-" else cents


def parse_line(line: str) -> Transaction | None:
    """One line -> Transaction, or None for blank/comment lines."""
    body, _, note = line.partition("#")
    body = body.strip()
    if not body:
        return None
    parts = _SPLIT.split(body)
    if len(parts) < 3:
        raise LedgerError(f"expected 'date  amount  payee' in {line!r}")
    when = date.fromisoformat(parts[0])
    return Transaction(when, parse_amount(parts[1]), " ".join(parts[2:]), note=note.strip())


def parse_lines(lines: Iterable[str]) -> list[Transaction]:
    out = []
    for n, line in enumerate(lines, 1):
        try:
            tx = parse_line(line)
        except (LedgerError, ValueError) as e:
            raise LedgerError(f"line {n}: {e}") from None
        if tx is not None:
            out.append(tx)
    return out
