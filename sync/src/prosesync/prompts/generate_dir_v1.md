You write the "prose code" for a directory: an English overview of what the directory contains and
how its parts fit together.

The input is a synthetic document with one numbered block per child (`[b1]`, `[b2]`, ...). Each
block is the summary paragraph of a file or of a subdirectory (a subdirectory's block starts with
its name ending in `/`). Write exactly one paragraph per block, in order, starting with `## <name>`
on its own line (the child's name exactly as given), then 1-3 sentences: what the child
contributes to this directory and how it relates to the others. Also write a `summary`: one
paragraph for the directory as a whole - its purpose and how the children fit together - for a
reader deciding where to look. No blank lines inside a paragraph. Identifiers in backticks.
