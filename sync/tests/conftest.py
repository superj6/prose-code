import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PY_CODE = '''import os
import sys

DEFAULT_RETRIES = 3


def fetch(url, retries=DEFAULT_RETRIES):
    for attempt in range(retries):
        try:
            return _get(url)
        except IOError:
            if attempt == retries - 1:
                raise


class Cache:
    """Tiny cache."""

    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)
'''

PY_PROSE = '''Import `os` and `sys`, and define `DEFAULT_RETRIES = 3`.

## fetch
Fetch `url` with `_get`, retrying up to `retries` times.
- Re-raise `IOError` on the last attempt.

## class Cache
A tiny dict-backed cache with `get(k)` returning `None` when missing.
'''
