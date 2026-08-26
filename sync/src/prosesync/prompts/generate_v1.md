You write "prose code": an English description of a source file, one paragraph per code block.

The code below is cut into numbered blocks (`[b1]`, `[b2]`, ...). Write exactly one paragraph for
each block, in order. A paragraph must not contain blank lines; it may contain bullet lines.

Style
- Imperative, behavioural English: what the block does, its inputs, outputs, defaults, and the
  edge cases the code actually handles - what a reader needs to reimplement it. Not syntax
  narration, not a restatement of each line.
- Be brief. Import/constant/boilerplate blocks: one short line. A simple function: one or two
  sentences. Use bullets (`- ...`) only for genuinely multi-step logic, one idea per bullet.
- Start a definition's paragraph with `## name` (or `## class Name`) on its own line.
- Identifiers in backticks.
- Do not mention error propagation, exceptions, or "invalid input" unless the block explicitly
  handles them. Do not repeat parameter defaults or facts already stated in another block. No
  filler phrases ("for use by", "in order to", "the resulting").
