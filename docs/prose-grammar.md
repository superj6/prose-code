# The prose-code contract

Prose code is lightly structured English. It is a **synchronised view** of the source file, not a
replacement for it: the code is what runs, the prose is what you read and edit when you would rather
say *what* than *how*. Both are committed. When you edit one side, the other side is updated so that
they describe the same program.

## Hard rule: one paragraph per block

The source file is partitioned into **blocks** (a function, a class, a run of imports, a group of
constants, a top-level statement...). The prose file has exactly one paragraph per block, in the same
order, separated by blank lines. This is what lets edits stay local: the paragraph for `parse_args`
is paired with the code for `parse_args`, so changing one only ever touches the other.

A paragraph may span several lines and may contain bullet points, but it must not contain a blank
line (a blank line starts the next paragraph, i.e. the next block).

## The summary block

The prose may begin with a **summary block**: a level-1 heading naming the file (`# calc.py`) and
one paragraph saying what the whole file is for, what it provides, and how the pieces fit. It has
no code of its own (block id `s`). Generation always writes one; syncs refresh it only when the
file's purpose or surface changes. Directory-level prose (`DIR.prose`, see below) is built from
these summaries.

## Directory prose

Every directory can carry a `DIR.prose`: a `# dirname/` summary block followed by **as many
paragraphs as the directory deserves** - purpose, how the children fit together, entry points,
what a newcomer should know. It is free-form: it does not have to list every file, but it must be
grounded in the directory's contents and use children's exact names in backticks.

Its "code side" is synthetic: one block per immediate child holding that child's summary and, for
subdirectories, an outline of everything beneath - the entire essence of the directory. The two
sides are synced without a one-to-one pairing: when a child's summary changes, only the paragraphs
whose claims are affected are rewritten (upward propagation); when you edit `DIR.prose`, the
children whose description changed get new summaries, and those flow into their code (push-down).
Naming a child that does not exist yet (`## new_module.py`, `## subdir/`) creates it from the
summary.

## Style

- Start a paragraph with an optional heading naming the symbol: `## parse_args` or `## class Cache`.
  Headings are for humans; the pairing is positional, not name-based.
- Write imperative, behavioural English: what the block does, its inputs, outputs, and edge cases.
  Do not narrate syntax ("declare a variable"); describe behaviour ("remember the last result").
- Put identifiers in backticks: `retries`, `Cache.get`, `os.path.join`.
- Use short bullet lists for multi-step logic. Keep one idea per bullet.
- Mention error handling, defaults, and anything a reader would need to reimplement the block.
- Leave out what the code makes obvious and what other blocks already say.

## What the sync model is told

- Emit edits only for blocks that changed (and, when unavoidable, their immediate neighbours).
- Preserve every other block byte-for-byte.
- Follow the file's existing style on the code side; follow this document on the prose side.
- Never add anchor comments, markers, or IDs to the code.
- When the prose side underdetermines the code (it usually does), keep the existing implementation
  and change only what the prose change implies.

## Example

```python
import os
import sys

DEFAULT_RETRIES = 3

def fetch(url, retries=DEFAULT_RETRIES):
    for attempt in range(retries):
        try:
            return _get(url)
        except IOError:
            if attempt == retries - 1:
                raise
```

```
Import `os` and `sys`, and define `DEFAULT_RETRIES = 3`.

## fetch
Fetch `url` with `_get`, retrying up to `retries` times (default `DEFAULT_RETRIES`).
- Swallow `IOError` on every attempt except the last, which is re-raised.
- Return the first successful result.
```
