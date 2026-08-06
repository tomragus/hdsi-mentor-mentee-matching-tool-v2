"""Fixed constants for the matching pipeline.

Everything here is deliberately a constant rather than a runtime setting, so a
run is reproducible from the inputs alone.
"""

from pathlib import Path

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

# Exact question text used to route rows to their special-case handlers.
LOCATION_QUESTION_PREFIX = "city, state, and country"
AVOID_QUESTION_PREFIX = "are there any topics, industries"
MENTEE_CAPACITY_QUESTION = "how many mentees would you like to be matched with?"

# Question text used to pull identity fields off a respondent's row. The email
# question is worded differently on the two forms, so it is matched on a
# keyword rather than in full.
NAME_QUESTION = "first & last name"
EMAIL_QUESTION_KEYWORD = "email address"

# Mentors who leave the capacity question blank take one mentee.
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
