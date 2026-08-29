# Examples

## `ledger/` — a small project to explore hierarchical prose

A personal-finance ledger: parse a text file of transactions, categorise them with rules, and
report on them. Small enough to read in ten minutes, structured enough to have a real tree:

```
ledger/
  DIR.prose              <- directory prose: what the project is and how the parts fit (free-form)
  ledger/                <- the Python package
    DIR.prose
    models.py            Transaction / Account (amounts in cents), validation
    parsing.py           the text format -> Transaction objects
    rules/               <- a subpackage: categorisation
      DIR.prose
      matchers.py        keyword / regex / amount matchers
      engine.py          Rule, categorise(), load_rules()
    reports.py           monthly totals, top categories, cent formatting
    cli.py               `python -m ledger.cli report sample.ledger --rules rules.txt`
  web/
    DIR.prose
    format.ts            cents/month formatting for the renderer
    render.ts            text and HTML rendering of a report
  tests/test_ledger.py   pytest (run from ledger/: `python -m pytest tests`)
  sample.ledger, rules.txt
```

The `.prose` files are checked in so you can open pairs immediately; the block maps under
`.prose/` are regenerated on first use.

### Tour

```sh
cd examples/ledger
python -m ledger.cli report sample.ledger --rules rules.txt      # what the code does
cat DIR.prose ledger/DIR.prose                                   # what the prose says about it
```

Then, in VS Code with the extension running (or with `prosesync sync … --changed …` on the CLI):

1. **Local edit, no propagation.** In `ledger/parsing.py`, make `parse_amount` also accept a
   thousands separator (`"1,500.00"`). Only the `## parse_amount` paragraph should change;
   `DIR.prose` should not be touched (the file's summary did not change).
2. **Surface change, upward propagation.** Add a public `amount_above(cents)` matcher to
   `ledger/rules/matchers.py`. The file summary gains it, then `ledger/rules/DIR.prose`'s
   `## matchers.py` paragraph follows; `ledger/DIR.prose` follows only if the `rules/` summary
   itself changed.
3. **Prose → code.** In `ledger/reports.py.prose`, change `## top_categories` to say ties are
   broken by most recent transaction instead of by name. Watch the code change; run the tests.
4. **Downward propagation.** In `ledger/DIR.prose`, say that `reports.py` also provides a
   `budget_status(transactions, limits)` helper comparing spend per category to a limit. Save,
   accept the push-down: `reports.py` gains the function and its prose gains a paragraph.
   Or name a child that does not exist yet — "`## export.py` writes a report as CSV" — and it is
   created from that sentence.
6. **The inverse.** Create `ledger/ledger/budget.py.prose` with just `# budget.py` and a summary
   sentence, save it: the code file appears, its prose gets proper paragraphs, and `DIR.prose`
   learns about it.
5. **Both sides at once.** Edit the code of `engine.py` and the prose of `## load_rules` before
   the debounce fires; both intents survive (two-pass sync).

## `snippets/` — the three files the eval set is built from

`calc.py`, `util.ts`, `main.go` — single-file cases used by `ml/data/eval_v1.jsonl`.
