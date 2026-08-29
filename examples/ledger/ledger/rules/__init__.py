"""Categorisation rules: a matcher decides whether a rule applies, the engine applies rules in order."""
from .engine import Rule, categorise, load_rules
from .matchers import Matcher, keyword, regex

__all__ = ["Matcher", "Rule", "categorise", "keyword", "load_rules", "regex"]
