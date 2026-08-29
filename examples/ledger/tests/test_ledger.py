from datetime import date

import pytest

from ledger import parse_lines, top_categories
from ledger.models import LedgerError, Transaction
from ledger.parsing import parse_amount
from ledger.reports import format_cents, monthly_totals
from ledger.rules import categorise, load_rules

LINES = [
    "2026-08-01  -12.50  Corner Cafe        # flat white",
    "2026-08-02  1500.00  ACME Payroll",
    "2026-08-03  -60.00  Metro Card",
    "2026-09-01  -12.50  Corner Cafe",
    "",
    "# a comment line",
]


def test_parse_amount_and_lines():
    assert parse_amount("-12.5") == -1250 and parse_amount("1500.00") == 150000 and parse_amount("+3") == 300
    with pytest.raises(LedgerError):
        parse_amount("12.345")
    txs = parse_lines(LINES)
    assert [t.payee for t in txs] == ["Corner Cafe", "ACME Payroll", "Metro Card", "Corner Cafe"]
    assert txs[0].note == "flat white" and txs[0].when == date(2026, 8, 1)
    with pytest.raises(LedgerError, match="line 1"):
        parse_lines(["2026-08-01  oops"])


def test_rules_and_reports():
    rules = load_rules(["food: cafe, restaurant", "transport: /metro|bus/", "# ignored"])
    txs = categorise(parse_lines(LINES), rules)
    assert [t.category for t in txs] == ["food", "uncategorised", "transport", "food"]
    assert top_categories(txs) == [("transport", 6000), ("food", 2500)]
    totals = monthly_totals(txs)
    assert list(totals) == ["2026-08", "2026-09"]
    assert totals["2026-08"] == {"in": 150000, "out": 7250, "net": 142750}
    assert format_cents(-1250) == "-12.50" and format_cents(5) == "0.05"


def test_transaction_validation():
    with pytest.raises(LedgerError):
        Transaction(date(2026, 1, 1), 0, "x")
    with pytest.raises(LedgerError):
        Transaction(date(2026, 1, 1), 100, "  ")
