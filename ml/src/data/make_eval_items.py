"""Turn hand-written (code, prose, edit) triples into eval items.

Edit ``CASES`` below, then:  .venv/bin/python ml/src/data/make_eval_items.py > ml/data/eval_v0.jsonl
Each case gives the synced pair, which side the user edited, the edited text, and the expected
text of the other side. Keep these small and human-checked; they are the frozen regression set.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "sync" / "src"))

CALC_CODE = (REPO / "examples" / "calc.py").read_text()
CALC_PROSE = """A tiny left-to-right expression calculator; import `math` and `operator`.

## OPS
Map the four operator symbols `+ - * /` to the matching `operator` functions.

## tokenize
Split `text` into a list of floats and operator symbols.
- Digits and `.` accumulate into a number; any other character flushes it.
- Operator characters become tokens; whitespace is skipped; anything else raises `ValueError`.

## evaluate
Fold `tokens` left to right with no precedence: start from the first number and apply each (operator, number) pair. Return `0.0` for an empty list.

## calc
Tokenize `text` and evaluate it.

## main
When run as a script, join the command-line arguments and print `calc` of them.
"""

CASES = [
    {
        "id": "calc-code-empty-guard",
        "language": "python",
        "changed_side": "code",
        "code_now": CALC_CODE.replace("    if not tokens:\n        return 0.0\n", "    if not tokens:\n        raise ValueError(\"empty expression\")\n"),
        "expected_contains": ["ValueError", "empty"],
    },
    {
        "id": "calc-prose-precedence-note",
        "language": "python",
        "changed_side": "prose",
        "prose_now": CALC_PROSE.replace("Tokenize `text` and evaluate it.", "Tokenize `text`, evaluate it, and round the result to 6 decimals."),
        "expected_contains": ["round("],
    },
    {
        "id": "calc-code-rename-constant",
        "language": "python",
        "changed_side": "code",
        "code_now": CALC_CODE.replace("OPS", "OPERATORS"),
        "expected_contains": ["OPERATORS"],
    },
]


def main() -> None:
    for c in CASES:
        item = {"id": c["id"], "language": c["language"], "prose": CALC_PROSE, "code": CALC_CODE, "changed_side": c["changed_side"]}
        for k in ("prose_now", "code_now", "expected", "expected_contains"):
            if k in c:
                item[k] = c[k]
        print(json.dumps(item))


if __name__ == "__main__":
    main()
