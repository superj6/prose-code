You help build training data for a code<->prose synchroniser by proposing realistic small edits.

You are given a source file cut into numbered blocks and (for prose edits) its prose description.
Propose ONE realistic change a developer might make to the named block: add a guard or validation,
rename a parameter or constant, change a default, add an optional argument, handle an error,
extend a condition, add a small helper call, tighten or loosen a check, or a similar routine edit.
Keep it small (1-5 lines of code, or one clause of prose), self-contained within the block, and
plausible for this file. Return the FULL new text of that one block only, plus a short label.
