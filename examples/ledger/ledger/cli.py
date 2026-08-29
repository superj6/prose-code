"""Command line: ``python -m ledger.cli report LEDGER_FILE [--rules RULES_FILE] [--top N]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import LedgerError
from .parsing import parse_lines
from .reports import format_cents, monthly_totals, top_categories
from .rules import categorise, load_rules


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledger")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="monthly totals and top expense categories")
    r.add_argument("ledger_file", type=Path)
    r.add_argument("--rules", type=Path, default=None)
    r.add_argument("--top", type=int, default=5)
    return p


def run_report(ledger_file: Path, rules_file: Path | None, top: int) -> str:
    transactions = parse_lines(ledger_file.read_text().splitlines())
    if rules_file is not None:
        transactions = categorise(transactions, load_rules(rules_file.read_text().splitlines()))
    lines = ["month     in        out       net"]
    for month, totals in monthly_totals(transactions).items():
        lines.append(f"{month}   {format_cents(totals['in']):>9} {format_cents(totals['out']):>9} {format_cents(totals['net']):>9}")
    lines.append("")
    lines.append("top categories")
    for category, cents in top_categories(transactions, top):
        lines.append(f"  {category:<16} {format_cents(cents):>9}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(run_report(args.ledger_file, args.rules, args.top))
    except LedgerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
