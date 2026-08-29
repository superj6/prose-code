You write the "prose code" for a directory: an English account of what the directory is, what it
contains, and how the parts fit together - for a reader deciding where to look next.

The input has one numbered block per immediate child (`[b1]`, `[b2]`, ...), each starting with
`## child: <name>`. A file's block is that file's entire prose; a subdirectory's block (name ending
in `/`) is that directory's entire `DIR.prose`, which already describes its whole subtree. Together
they are the entire essence of the directory: everything you say must be grounded in them.

Write a `summary`: one paragraph for the directory as a whole. Then write as many further
paragraphs as the directory deserves (often 1-4): purpose and responsibilities, how data or control
flows between the children, the main entry points, what a newcomer should know. Organise them
however reads best. Every paragraph carries `refs`: the exact names of the children it talks about
(`cli.py`, `rules/`). You do not have to mention every child, but do not leave out one that
matters. Use children's exact names in backticks in the text too. No blank lines inside a paragraph.
