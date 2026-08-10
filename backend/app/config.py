"""Fixed constants, and the one string-normalization function.

Everything here is deliberately a constant rather than a runtime setting, so a
run is reproducible from the inputs alone.

`normalize` is at the bottom. The questions database and the form exports
differ in invisible ways: the database contains non-breaking hyphens, and
exports commonly carry smart quotes and trailing spaces. Comparing raw strings
would read those differences as a mismatch -- a listed option would look like a
write-in, and a question would look missing from its export. Original text is
always kept for display; only comparisons use these forms.
"""

import re
import unicodedata
from pathlib import Path
from typing import Any

# The questions database ships with the repository rather than being uploaded:
# it is the schema both exports are read against, not data for a given cycle.
QUESTIONS_DATABASE = (
    Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"
)

# Seed for tie-breaking in the assignment step, so repeated runs on the same
# inputs produce the same matches.
RANDOM_SEED = 20260805

EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"

# Point scale before the weight multiplier is applied.
PERFECT_MATCH_POINTS = 10
GOOD_MATCH_POINTS = 5
NO_MATCH_POINTS = 0

# Subtracted from a question's contribution after weighting, whenever the
# mentor, the mentee, or both answered it with a write-in option.
WRITE_IN_PENALTY = 5

# Used when a question's "Similarity Percentile Cutoffs" cell is blank.
DEFAULT_PERCENTILES = (85, 50)

# Hour differences (relative to Pacific Time) that earn each point value.
LOCATION_PERFECT_MAX_HOURS = 0
LOCATION_GOOD_MAX_HOURS = 2

# The order questions appear in when a coordinator opens a match or a person.
# Database row numbers, following the mentor form. The mentee form is the same
# sequence without the mentor-only rows (22 Data Science Program, 23 Job Title,
# 24 How many mentees), which drop out on their own since a mentee never
# answered them. Display only -- nothing about scoring reads this.
DISPLAY_ORDER = (
    19, 20, 21, 22, 23, 6, 7, 1, 2, 8, 13, 14,
    3, 4, 5, 15, 9, 10, 11, 16, 17, 24, 12, 18,
)

# Exact question text used to route rows to their special-case handlers.
LOCATION_QUESTION_PREFIX = "city, state, and country"
AVOID_QUESTION_PREFIX = "are there any topics, industries"
MENTEE_CAPACITY_QUESTION = "how many mentees would you like to be matched with?"

# Question text used to pull identity fields off a respondent's row. The email
# question is worded differently on the two forms, so it is matched on a
# keyword rather than in full.
NAME_QUESTION = "first & last name"
EMAIL_QUESTION_KEYWORD = "email address"

# The capacity of anyone whose form does not ask. That is every mentee, since
# the question is mentor-only, so this is what they all get. It is also the
# fallback for a mentor who left it blank, which the current form prevents.
DEFAULT_MENTOR_CAPACITY = 1

# The questions whose answers make up the controlled vocabulary that avoid
# responses are resolved against, matched on the mentor wording.
VOCABULARY_QUESTIONS = (
    "describe your industry",
    "sub-domains",
    "tools or platforms",
)

# Single letters and two-character terms are dropped from the vocabulary. "R"
# and "AI" would otherwise match almost every respondent, so one avoid answer
# mentioning either would block that person against the whole cohort.
MIN_VOCABULARY_TERM_LENGTH = 3
MAX_VOCABULARY_TERM_WORDS = 4

# NFKC leaves most of these as-is, so they need an explicit mapping to ASCII.
# Three of them (U+2011, U+00B4, U+2033) it does fold on its own; they stay
# listed so each set reads as the whole family rather than a subset.
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
