You write "prose code": an English description of a source file, one paragraph per code block.

The code below is cut into numbered blocks (`[b1]`, `[b2]`, ...). Write exactly one paragraph for
each block, in order. A paragraph must not contain blank lines; it may contain bullet lines.

Style
- Imperative, behavioural English: what the block does, its inputs, outputs, defaults, error
  handling and edge cases - what a reader would need to reimplement it. Not syntax narration.
- Start a definition's paragraph with `## name` (or `## class Name`) on its own line.
- Identifiers in backticks. Short bullets (`- ...`) for multi-step logic, one idea per bullet.
- Leave out what the code makes obvious and what other blocks already say. Import/constant blocks
  get one short line.
