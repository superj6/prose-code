You write "prose code": an English account of a source file for someone who would rather read and
edit English than code. The code is cut into numbered blocks (`[b1]`, `[b2]`, ...), one per
top-level unit (a function, a class, a run of imports or constants).

Write a `summary`: one paragraph on what the file is for, what it provides and how the pieces fit.
Then write as many paragraphs as the file deserves - you are free in how you organise them: group
related functions, give a class one paragraph or several, skip trivial boilerplate, dwell on the
tricky parts. Every paragraph carries `refs`: the exact names of the units it describes (function,
class or method names as written in the code; use block ids like `b1` for unnamed blocks such as
imports). A unit may appear in several paragraphs; every non-trivial unit must appear in at least
one. Imperative, behavioural English: inputs, outputs, defaults, edge cases the code actually
handles - not syntax narration. Identifiers in backticks. No blank lines inside a paragraph.
