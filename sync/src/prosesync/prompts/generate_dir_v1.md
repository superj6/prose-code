You write the "prose code" for a directory: an English account of what the directory is, what it
contains, and how the parts fit together - for a reader deciding where to look next.

The input is a synthetic document with one numbered block per immediate child (`[b1]`, `[b2]`, ...).
A file's block holds that file's summary; a subdirectory's block (its name ends in `/`) holds the
subdirectory's summary followed by an outline of everything beneath it. Together they are the
entire essence of the directory: everything you say must be grounded in them.

Write a `summary`: one paragraph for the directory as a whole. Then write as many further
paragraphs as the directory deserves (often 1-4): purpose and responsibilities, how data or control
flows between the children, the main entry points, and anything a newcomer should know. You are
free in how you organise this - group children by role, skip the trivial, dwell on the important.
You do not have to mention every file, but do not leave out a child that matters, and use each
child's exact name in backticks when you refer to it. No blank lines inside a paragraph.
