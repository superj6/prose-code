# ruff: noqa: C408  (dict(...) reads better than braces for the case table)
"""Build the eval set from prose fixtures + hand-written perturbations.

    .venv/bin/python ml/src/data/make_eval_items.py > ml/data/eval_v1.jsonl

Each CASE names a fixture (code in examples/ or ml/data/fixtures/, prose in ml/data/fixtures/),
the side the user edited, one or more literal substitutions applied to that side (each must
match exactly once), optional substitutions on the *other* side (both-sides-dirty cases), and
the checks on the model's output: ``expect`` = substrings that must appear (a list inside the
list means "any of these"), ``absent`` = substrings that must not appear.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "ml" / "data" / "fixtures"


def fixture(name: str) -> tuple[str, str, str]:
    code_path = REPO / "examples" / "snippets" / name
    if not code_path.exists():
        code_path = FIX / f"{name}.txt"  # fixtures-only sources carry .txt so linters skip them
    lang = {".py": "python", ".ts": "typescript", ".go": "go"}[pathlib_suffix(name)]
    return code_path.read_text(), (FIX / f"{name}.prose").read_text(), lang


def pathlib_suffix(name: str) -> str:
    return Path(name).suffix


def sub(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        assert text.count(old) == 1, f"substitution must match exactly once: {old!r} ({text.count(old)})"
        text = text.replace(old, new)
    return text


CASES = [
    # ---------------------------------------------------------------- calc.py (python, 6 blocks)
    dict(id="calc-code-empty-raises", file="calc.py", side="code",
         edit=[("    if not tokens:\n        return 0.0\n", "    if not tokens:\n        raise ValueError(\"empty expression\")\n")],
         expect=["ValueError", ["empty", "Empty"]], absent=["0.0"]),
    dict(id="calc-prose-round", file="calc.py", side="prose",
         edit=[("Tokenize `text` and pass the tokens to `evaluate`, returning the calculated value.",
                "Tokenize `text`, pass the tokens to `evaluate`, and return the value rounded to 6 decimal places.")],
         expect=["round("]),
    dict(id="calc-code-rename-ops", file="calc.py", side="code",
         edit=[("OPS = {", "OPERATORS = {"), ("if ch in OPS:", "if ch in OPERATORS:"), ("OPS[op]", "OPERATORS[op]")],
         expect=["OPERATORS"], absent=["`OPS`"]),
    dict(id="calc-code-add-function-split", file="calc.py", side="code",
         edit=[("def calc(text):", "def parse(text):\n    \"\"\"Tokens of text, or [] on error.\"\"\"\n    try:\n        return tokenize(text)\n    except ValueError:\n        return []\n\n\ndef calc(text):")],
         expect=["## parse", "## calc"]),
    dict(id="calc-code-delete-function", file="calc.py", side="code",
         edit=[("def calc(text):\n    return evaluate(tokenize(text))\n\n\n", ""), ("print(calc(", "print(evaluate(tokenize(")],
         expect=[], absent=["## calc"]),
    dict(id="calc-prose-comma-separator", file="calc.py", side="prose",
         edit=[("allowing digits, decimal points, and whitespace.", "allowing digits, decimal points, and whitespace; commas are skipped like whitespace.")],
         expect=[['","', "','", "ch == ','", "ch == \",\""]]),
    dict(id="calc-code-typeerror", file="calc.py", side="code",
         edit=[("raise ValueError(f\"unexpected character {ch!r}\")", "raise TypeError(f\"unexpected character {ch!r}\")")],
         expect=["TypeError"], absent=["ValueError"]),
    dict(id="calc-both-dirty", file="calc.py", side="code",
         edit=[("raise ValueError(f\"unexpected character {ch!r}\")", "raise TypeError(f\"unexpected character {ch!r}\")")],
         other_edit=[("returning the calculated value.", "returning the value rounded to 6 decimals.")],
         expect=["TypeError", "rounded to 6 decimals"]),
    # ---------------------------------------------------------------- util.ts (typescript, 5 blocks)
    dict(id="ts-code-add-field", file="util.ts", side="code",
         edit=[("  verbose: boolean;\n}", "  verbose: boolean;\n  timeoutMs: number;\n}"),
               ("{ retries: 3, verbose: false }", "{ retries: 3, verbose: false, timeoutMs: 1000 }")],
         expect=["timeoutMs", "1000"]),
    dict(id="ts-prose-warn-on-retry", file="util.ts", side="prose",
         edit=[("Call `fn` up to `times` attempts and return the first successful result.",
                "Call `fn` up to `times` attempts and return the first successful result; log each failed attempt with `console.warn`.")],
         expect=["console.warn"]),
    dict(id="ts-prose-missing-file", file="util.ts", side="prose",
         edit=[("so supplied values override the defaults.", "so supplied values override the defaults; if the file does not exist, return `DEFAULTS` unchanged.")],
         expect=[["existsSync", "catch", "ENOENT"]]),
    dict(id="ts-code-rename-param", file="util.ts", side="code",
         edit=[("fn: () => T, times: number", "fn: () => T, attempts: number"), ("i < times", "i < attempts")],
         expect=["`attempts`"], absent=["`times`"]),
    # ---------------------------------------------------------------- main.go (go, 4 blocks)
    dict(id="go-prose-ignore-punct", file="main.go", side="prose",
         edit=[("whitespace-only or empty strings produce zero.", "whitespace-only or empty strings produce zero; words made only of punctuation are not counted.")],
         expect=[["unicode", "IsPunct", "punct"]]),
    dict(id="go-code-usage-text", file="main.go", side="code",
         edit=[("usage: main <text>", "usage: main <words...>")],
         expect=["<words...>"], absent=["<text>"]),
    dict(id="go-code-inline-wordcount", file="main.go", side="code",
         edit=[("// wordCount returns the number of whitespace-separated words in s.\nfunc wordCount(s string) int {\n\treturn len(strings.Fields(s))\n}\n\n", ""),
               ("fmt.Println(wordCount(strings.Join(os.Args[1:], \" \")))", "fmt.Println(len(strings.Fields(strings.Join(os.Args[1:], \" \"))))")],
         expect=[], absent=["## wordCount"]),
    # ---------------------------------------------------------------- helpers.py (python, 26 blocks -> windowed)
    dict(id="long-code-median-guard", file="helpers.py", side="code",
         edit=[("    return a + b * 9\n", "    if b == 0:\n        return a\n    return a + b * 9\n")],
         expect=[["`0`", "zero", " 0"]]),
    dict(id="long-prose-fib-cache", file="helpers.py", side="prose",
         edit=[("## fib\n", "## fib\nCache results in a module-level dict keyed by `(a, b)` and return the cached value on repeat calls. ")],
         expect=[["cache", "_cache", "memo", "{}"]]),
    dict(id="long-code-rename-fact", file="helpers.py", side="code",
         edit=[("def fact(a, b=1):", "def factorial(a, b=1):"), ('"""fact of a and b."""', '"""factorial of a and b."""')],
         expect=["factorial"]),
]


def main() -> None:
    for c in CASES:
        code, prose, lang = fixture(c["file"])
        item = {"id": c["id"], "language": lang, "prose": prose, "code": code, "changed_side": c["side"],
                "expected_contains": c.get("expect", []), "expected_absent": c.get("absent", [])}
        if c["side"] == "code":
            item["code_now"] = sub(code, c["edit"])
            if c.get("other_edit"):
                item["prose_now"] = sub(prose, c["other_edit"])
                item["other_side_dirty"] = True
        else:
            item["prose_now"] = sub(prose, c["edit"])
            if c.get("other_edit"):
                item["code_now"] = sub(code, c["other_edit"])
                item["other_side_dirty"] = True
        print(json.dumps(item))
    print(f"{len(CASES)} items", file=sys.stderr)


if __name__ == "__main__":
    main()
