# Phase 0 spike results — 2026-08-26

Model: `gpt-5.6-luna` via the OpenAI Responses API, strict JSON-schema output, `configs/base.yaml`
defaults (prompt `v1`, `context_blocks: 1`). All runs through `prosesync gen|sync` on
`examples/`. "Minimal" = only the affected block(s) changed on the target side (checked by diff).

| # | file | direction | edit made | result | ms |
|---|---|---|---|---|---|
| 1 | calc.py | gen | — | 6 blocks, accurate paragraphs incl. edge cases | 6372 |
| 2 | calc.py | code → prose | `evaluate` raises on dangling operator | ✅ only `## evaluate` paragraph rewritten, mentions the new `ValueError` | 3846 |
| 3 | calc.py | prose → code | "return the result rounded to 6 decimal places" | ✅ one-line change `round(..., 6)`; file runs | 3200 |
| 4 | calc.py | code → prose | add a new `parse()` function between `evaluate` and `calc` | ✅ block b4 split into b4 + b7, new `## parse` paragraph inserted in the right place | 4194 |
| 5 | calc.py | code → prose | delete `parse()` again | ✅ `delete b7`, paragraph removed, map back to 6 blocks | 3033 |
| 6 | util.ts | gen | — | 5 blocks | 5155 |
| 7 | util.ts | code → prose | add `timeoutMs` to `Config` and `DEFAULTS` | ✅ two blocks updated (`## Config`, `## DEFAULTS`), rest untouched | 3888 |
| 8 | util.ts | prose → code | "log each failed attempt with `console.warn` including the attempt number" | ✅ one line added inside the catch; `tsc --noEmit` passes | 4145 |
| 9 | main.go | gen | — | 4 blocks | 3713 |
| 10 | main.go | prose → code | "ignores any word that is only punctuation" | ✅ `wordCount` rewritten with `unicode.IsPunct`, `"unicode"` added to the neighbouring import block; tree-sitter syntax ok (no `go` toolchain here) | **16140** |
| 11 | eval `calc-code-empty-guard` | code → prose | empty list now raises | ✅ | 3271 |
| 12 | eval `calc-prose-precedence-note` | prose → code | round to 6 decimals | ✅ syntax valid | 1663 |
| 13 | eval `calc-code-rename-constant` | code → prose | `OPS` → `OPERATORS` | ❌ first run: 0 edits. Added the "names are part of the contract" rule to `system_v1.md` → ✅ heading and mentions renamed | 2673 / 3293 |

**Score: 10/10 hand-made edits after one prompt fix (12/13 attempts).** Zero collateral edits, zero
schema/apply warnings in every run. Phase 0 definition of done is met.

Observations
- Latency is 1.7–4.2 s for single-block edits (~1.3k input tokens, ~170 output). The Go rewrite took
  16 s — larger output plus the model touching two blocks. "Near real time" needs either a
  faster/smaller model (Phase 4) or a token-level preview while the edit streams.
- The generated prose is good but verbose ("propagating any tokenization or arithmetic errors"
  appears in several paragraphs). Worth a style pass on `generate_v1.md` before it becomes
  training data.
- The model correctly used the *neighbour* allowance (import block) for the Go change; the
  `context_blocks: 1` default is right.
- Prose→code is underdetermined as expected: "case-insensitive" in the Go request was (correctly)
  ignored since counting never depended on case.

## Sample: generated prose for `examples/calc.py` (after edits 2–3)

```
Import `math` and `operator` for use by the calculator.

Define `OPS`, mapping the four supported operator symbols to their corresponding arithmetic functions.

## tokenize
Convert an expression string into a sequence of floating-point numbers and operator symbols. Accumulate consecutive digits and decimal points as one number, emit it when a non-number character is reached, accept whitespace as a separator, append recognized operators from `OPS`, and raise `ValueError` for any other character. Convert numeric text with `float`; an empty input produces an empty token list, while malformed numeric forms may raise the conversion error from `float`.

## evaluate
Evaluate the token sequence from left to right without operator precedence. Return `0.0` for an empty sequence, raise `ValueError` for an even-length sequence ending with a dangling operator, use the first token as the initial result, and apply each subsequent operator to the following value through `OPS`; invalid token arrangements or arithmetic errors propagate.

## calc
Tokenize `text`, pass the tokens to `evaluate`, and return the result rounded to 6 decimal places; tokenization or arithmetic errors propagate.

When run as a script, import command-line arguments, join them with spaces into one expression, calculate it with `calc`, and print the result; with no arguments the joined expression is empty and prints `0.0`, while errors are not caught.
```

# Eval set v1 — 2026-08-28

`make eval` on `ml/data/eval_v1.jsonl` (18 cases: python/typescript/go, code→prose and prose→code,
rename, add-function split, delete, both-sides-dirty, and three cases on a 26-block file that
exercise windowed context). Model `gpt-5.6-luna`, `reasoning_effort: low`, prompt `v1`.

| run | n | errors | correct | collateral | schema_valid | syntax_valid | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|
| first run | 18 | 0 | 0.944 (17/18) | 0.019 | 1.0 | 1.0 | 2983 | 7945 |
| after both-dirty fix | 18 | 0 | **1.0** | **0.0** | 1.0 | 1.0 | 2746 | 4856 |

The single miss was `calc-both-dirty`: asked to sync a code change while the user had also edited
a prose paragraph, the model "reconciled" that paragraph back to match the code — erasing user
text. Fix (structural, not just prompt): a both-dirty request now runs two passes. Pass 1 applies
the primary change with the user-edited target blocks marked off-limits; pass 2 applies the user's
other-side edits to the primary side on top of pass 1. Both intents land, nothing is reverted, and
the extension applies edits per side.

Latency: prose→code cases that generate new code are the slow tail (4–8 s); code→prose sits at
1.5–3 s. `cache_hit` is 0 here because every item is a distinct pair; repeated syncs of one pair
hit ~99 % (see README, "Latency knobs").
