"""A tiny expression calculator."""
import math
import operator

OPS = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}


def tokenize(text):
    """Split an expression into numbers and operator symbols."""
    tokens = []
    number = ""
    for ch in text:
        if ch.isdigit() or ch == ".":
            number += ch
        else:
            if number:
                tokens.append(float(number))
                number = ""
            if ch in OPS:
                tokens.append(ch)
            elif not ch.isspace():
                raise ValueError(f"unexpected character {ch!r}")
    if number:
        tokens.append(float(number))
    return tokens


def evaluate(tokens):
    """Evaluate left to right, no precedence."""
    if not tokens:
        return 0.0
    result = tokens[0]
    for op, value in zip(tokens[1::2], tokens[2::2]):
        result = OPS[op](result, value)
    return result


def calc(text):
    return evaluate(tokenize(text))


if __name__ == "__main__":
    import sys

    print(calc(" ".join(sys.argv[1:])))
