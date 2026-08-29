# The prose-code contract

Prose code is free-form English about a source file (or a directory) that stays in sync with it.
It is a **synchronised view**, not a replacement: the code is what runs, the prose is what you read
and edit when you would rather say *what* than *how*. Both are committed. When you edit one side,
the other side is updated so that they describe the same program.

## Shape of a prose file

```
# calc.py
One paragraph: what the file is for, what it provides, how the pieces fit.

## tokenize
Scan `text` into float operands and operator symbols ...

## evaluate, calc
Fold the tokens left to right without precedence; `calc` is the one-call convenience ...

## OPS, b1
The operator table and the imports it needs.
```

- The first paragraph is the **summary block**: a level-1 heading naming the file, then one
  paragraph. Generation always writes one; syncs refresh it only when the file's purpose or
  surface changes. Directory prose is built from it and from everything below it.
- Every other paragraph starts with an **annotation line**: `## ` followed by the exact names of
  the code units it describes (functions, classes, methods; a block id such as `b1` for unnamed
  blocks like imports). A unit may be covered by several paragraphs and a paragraph may cover
  several units. Otherwise the prose is free: as many paragraphs as the file deserves, organised
  however reads best, skipping the trivial.
- Paragraphs are separated by blank lines and contain none. Bullets are fine.

## Why annotations

They are the only structure the sync needs. When code changes, the changed units' names select the
paragraphs that may be rewritten; when a paragraph changes, its annotation selects the code blocks
that may be edited - and only those blocks are sent to the model in full, the rest collapsed. An
unannotated paragraph falls back to "anything may be relevant", which costs more and is less
precise, so keep annotations exact and update them when units are renamed, added or removed.

## Directory prose

Every directory can carry a `DIR.prose`: a `# dirname/` summary block followed by as many
annotated paragraphs as the directory deserves - purpose, how the children fit together, entry
points, what a newcomer should know. Here annotations name **children** (`## cli.py, rules/`).

Its "code side" is synthetic: one block per immediate child holding that child's **entire prose**
(a file's prose file, or a subdirectory's `DIR.prose`, which already encapsulates its own subtree).
When a child's prose changes, only the paragraphs annotated with that child are rewritten (upward
propagation); when you edit `DIR.prose`, only the children named by the changed paragraphs get
their prose rewritten, and those changes flow into their code (push-down). Naming a child that does
not exist yet (`export.py`, `util/`) creates it.

## Style

- Imperative, behavioural English: what a unit does, its inputs, outputs, defaults and the edge
  cases the code actually handles. Describe behaviour, not syntax.
- Identifiers in backticks: `retries`, `Cache.get`, `os.path.join`.
- Short bullets for multi-step logic, one idea per bullet.
- Leave out what the code makes obvious and what another paragraph already says.

## What the sync model is told

- Emit block-level edits only (`replace` / `delete`), restricted to the editable blocks; everything
  else is preserved verbatim by the system.
- Change as little as possible: no rewording, reformatting, renaming or "improving" beyond what the
  change requires. Return no edits when the sides already agree.
- Code side: follow the file's existing style; never add markers or comments about blocks. When the
  prose underdetermines the code (it usually does), keep the existing implementation.
- Prose side: keep annotations exact; add a `## names` line to any paragraph you add.

`sync.file_mode: paired` restores the earlier strict form (exactly one paragraph per code block,
no annotations); it is what the eval set and the training records currently use.
