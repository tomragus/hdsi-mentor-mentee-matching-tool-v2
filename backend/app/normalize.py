"""The single string-normalization function used for every comparison.

The questions database and the form exports differ in invisible ways: the
database contains non-breaking hyphens, and exports commonly carry smart quotes
and trailing spaces. Comparing raw strings would read those differences as a
mismatch — a listed option would look like a write-in, and a question would look
missing from its export.

Original text is always kept for display; only comparisons use these forms.
"""

import re
import unicodedata
from typing import Any

# NFKC leaves these as-is, so they need an explicit mapping to ASCII.
_DASH_VARIANTS = "‐‑‒–—―−"
_SINGLE_QUOTE_VARIANTS = "‘’‚‛′´`"
_DOUBLE_QUOTE_VARIANTS = "“”„‟″"

_ASCII_EQUIVALENTS = {ord(c): "-" for c in _DASH_VARIANTS}
_ASCII_EQUIVALENTS.update({ord(c): "'" for c in _SINGLE_QUOTE_VARIANTS})
_ASCII_EQUIVALENTS.update({ord(c): '"' for c in _DOUBLE_QUOTE_VARIANTS})

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize(text: Any) -> str:
    """Return the comparison form of a string.

    Anything that isn't a string (None, or the NaN pandas puts in empty cells)
    normalizes to the empty string, so blank responses compare equal.
    """
    if not isinstance(text, str):
        return ""

    result = unicodedata.normalize("NFKC", text)
    result = result.casefold()
    result = result.strip()
    result = _WHITESPACE_RUN.sub(" ", result)
    return result.translate(_ASCII_EQUIVALENTS)


def is_blank(text: Any) -> bool:
    """True when a cell holds no response."""
    return normalize(text) == ""
