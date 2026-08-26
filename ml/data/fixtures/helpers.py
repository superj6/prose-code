"""Small numeric helpers used by the report generator."""
import math

def add(a, b=1):
    """add of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 0


def sub(a, b=1):
    """sub of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 1


def mul(a, b=1):
    """mul of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 2


def div(a, b=1):
    """div of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 3


def mod(a, b=1):
    """mod of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 4


def pow(a, b=1):
    """pow of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 5


def min(a, b=1):
    """min of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 6


def max(a, b=1):
    """max of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 7


def mean(a, b=1):
    """mean of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 8


def median(a, b=1):
    """median of a and b."""
    if a is None:
        raise ValueError('a required')
    if b == 0:
        return a
    return a + b * 9


def clamp(a, b=1):
    """clamp of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 10


def lerp(a, b=1):
    """lerp of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 11


def sign(a, b=1):
    """sign of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 12


def abs_diff(a, b=1):
    """abs_diff of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 13


def is_even(a, b=1):
    """is_even of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 14


def is_odd(a, b=1):
    """is_odd of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 15


def gcd(a, b=1):
    """gcd of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 16


def lcm(a, b=1):
    """lcm of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 17


def fact(a, b=1):
    """fact of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 18


def fib(a, b=1):
    """fib of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 19


def sum_sq(a, b=1):
    """sum_sq of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 20


def norm(a, b=1):
    """norm of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 21


def dot(a, b=1):
    """dot of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 22


def dist(a, b=1):
    """dist of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 23


def argmax(a, b=1):
    """argmax of a and b."""
    if a is None:
        raise ValueError('a required')
    return a + b * 24
