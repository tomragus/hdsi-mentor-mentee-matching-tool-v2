"""Fixed constants, and the one string-normalization function.

Everything here is a constant rather than a runtime setting, so a run is
reproducible from the inputs alone.

`normalize` exists because the questions database and the form exports differ
in invisible ways -- non-breaking hyphens in the database, smart quotes and
trailing spaces in the exports. Original text is always kept for display; only
comparisons use the normalized form.
"""

import re
import unicodedata
from pathlib import Path
from typing import Any

# Ships with the repository rather than being uploaded: it is the schema both
# exports are read against, not data for a given cycle.
QUESTIONS_DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"

# Seeds tie-breaking in the assignment step, so repeated runs match.
RANDOM_SEED = 20260805

EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"

# Point scale before the weight multiplier.
PERFECT_MATCH_POINTS = 10
GOOD_MATCH_POINTS = 5
NO_MATCH_POINTS = 0

# Subtracted from a question's contribution after weighting, whenever either
# side answered it with a write-in.
WRITE_IN_PENALTY = 5

# Added to a pair's raw score, on top of scoring, when both sides name the same
# city. Like the write-in penalty, this never touches the maximum a pair could
# have scored.
CITY_MATCH_BONUS = 30

# Used when a question's "Similarity Percentile Cutoffs" cell is blank.
DEFAULT_PERCENTILES = (85, 50)

# Hour differences from Pacific Time that earn each point value.
LOCATION_PERFECT_MAX_HOURS = 0
LOCATION_GOOD_MAX_HOURS = 2

# Reading order for a match or a person, as database row numbers following the
# mentor form. Mentor-only rows (22 Program, 23 Job Title, 24 How many mentees)
# drop out on the mentee side on their own. Display only.
DISPLAY_ORDER = (
    19, 20, 21, 22, 23, 6, 7, 1, 2, 8, 13, 14,
    3, 4, 5, 15, 9, 10, 11, 16, 17, 24, 12, 18,
)

# Question text used to route rows to their special-case handlers, and to pull
# identity fields off a row. Email is matched on a keyword, since the two forms
# word it differently.
LOCATION_QUESTION_PREFIX = "city, state, and country"
AVOID_QUESTION_PREFIX = "are there any topics, industries"
MENTEE_CAPACITY_QUESTION = "how many mentees would you like to be matched with?"
NAME_QUESTION = "first & last name"
EMAIL_QUESTION_KEYWORD = "email address"

# Question text for the two hard disqualification checks. Both rows keep their
# normal scored role -- these prefixes only locate the row for a constraint
# applied on top of scoring, not a change to how it's scored.
COMMITMENT_QUESTION_PREFIX = "are you able to commit to monthly"
COMMUNICATION_QUESTION_PREFIX = "please select your preferred methods of communication"

# Fewer shared communication methods than this disqualifies the pair outright,
# regardless of score.
MIN_COMMUNICATION_OVERLAP = 2

# The capacity of anyone whose form does not ask -- every mentee, plus any
# mentor who left it blank.
DEFAULT_MENTOR_CAPACITY = 1

# The questions whose answers make up the vocabulary avoid responses resolve
# against, matched on the mentor wording.
VOCABULARY_QUESTIONS = ("describe your industry", "sub-domains", "tools or platforms")

# "R" and "AI" would match nearly every respondent, so one avoid answer naming
# either would block that person against the whole cohort.
MIN_VOCABULARY_TERM_LENGTH = 3
MAX_VOCABULARY_TERM_WORDS = 4

# NFKC folds three of these on its own; the rest need an explicit mapping, and
# each family is listed whole so it reads as the set rather than a subset.
_ASCII_EQUIVALENTS = {
    **{ord(c): "-" for c in "‐‑‒–—―−"},
    **{ord(c): "'" for c in "‘’‚‛′´`"},
    **{ord(c): '"' for c in "“”„‟″"},
}

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize(text: Any) -> str:
    """The comparison form of a string. Anything that isn't a string -- None, or the NaN
    pandas puts in empty cells -- becomes "", so blank responses compare equal.
    """
    if not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold().strip()
    return _WHITESPACE_RUN.sub(" ", folded).translate(_ASCII_EQUIVALENTS)


def is_blank(text: Any) -> bool:
    """True when a cell holds no response."""
    return normalize(text) == ""
