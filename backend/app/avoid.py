"""Resolving avoid responses into terms from a controlled vocabulary.

Comparing two avoid answers to each other would reward people who happen to
want to avoid the same thing, which says nothing about whether they would work
well together. So this question is handled as a preprocessing step instead:
each answer is resolved into concrete terms, and Step 13 checks those terms
against what the other party actually said they work on.

The vocabulary is closed and comes from the surveys themselves -- the
industries, sub-domains, and tools people listed on the other questions.
Matching against a closed vocabulary rather than by sentence similarity is what
keeps this rare and specific: it fires on genuine overlap, not on vibe.

Extraction is pluggable. The default reads terms straight out of the text,
which is enough to be correct on plain answers like "prefer to avoid finance".
The Anthropic-backed extractor, which handles phrasing the keyword reader
misses, plugs in at the `extractor` argument.
"""

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.config import (
    MAX_VOCABULARY_TERM_WORDS,
    MIN_VOCABULARY_TERM_LENGTH,
    VOCABULARY_QUESTIONS,
)
from app.normalize import is_blank, normalize
from app.questions import Question
from app.inputs import Respondent

logger = logging.getLogger(__name__)

# An extractor takes the answer and the vocabulary and returns the terms it
# found, or None if it could not produce an answer at all.
Extractor = Callable[[str, tuple[str, ...]], set[str] | None]

# Ways of saying "nothing to avoid". Anything longer is left to the extractor,
# which will resolve prose like "no topics I'd prefer to avoid" to no terms.
NULL_ANSWERS = frozenset(
    {
        "", "-", "--", ".", "n/a", "na", "no", "nope", "none", "nothing",
        "no thanks", "not really", "not at all", "no preference",
        "no preferences", "nil", "n.a.", "none that i can think of",
    }
)

# Words that are structure or filler rather than a term worth matching on.
JUNK_TERMS = frozenset(
    {
        "etc", "e.g", "eg", "ie", "other", "others", "option", "options",
        "check box", "check boxes", "checkbox", "maybe", "and", "or", "the",
        "data", "tech", "misc", "various", "many", "any", "all", "none",
        "more", "some", "select up to 3", "select up to",
    }
)

# Inline enumeration, as in "1. AI Agents 2. NLP 3. MLOps".
_ENUMERATION = re.compile(r"(?:^|\s)\d+\s*[.)]\s*")
_TERM_SEPARATORS = re.compile(r"[,;/&\n|]+")
# The example lists the questions give, as in "(Python, R, TensorFlow, etc.)".
_PARENTHETICAL = re.compile(r"\(([^)]*)\)")


def _clean_terms(text: str) -> list[str]:
    """Split one cell into candidate vocabulary terms."""
    if is_blank(text):
        return []

    # Parentheses hold sub-lists ("Python (pandas, scikit-learn)"), so they
    # separate terms rather than enclosing one.
    flattened = _ENUMERATION.sub(", ", text.replace("(", ",").replace(")", ","))

    terms = []
    for part in _TERM_SEPARATORS.split(flattened):
        term = normalize(part).strip(" .-'\"")
        if not term or "?" in term:
            continue
        if len(term) < MIN_VOCABULARY_TERM_LENGTH:
            continue
        if len(term.split()) > MAX_VOCABULARY_TERM_WORDS:
            continue
        if term in JUNK_TERMS or term.isdigit():
            continue
        terms.append(term)
    return terms


def _vocabulary_questions(questions: list[Question]) -> list[Question]:
    return [
        question
        for question in questions
        if any(
            keyword in normalize(question.mentor_question)
            for keyword in VOCABULARY_QUESTIONS
        )
    ]


def build_vocabulary(
    questions: list[Question], respondents: Iterable[Respondent]
) -> tuple[str, ...]:
    """Collect every industry, sub-domain, and tool this cohort mentioned.

    Both sources count: the examples the questions themselves give, and what
    people actually wrote.
    """
    rows = _vocabulary_questions(questions)
    terms: set[str] = set()

    for question in rows:
        for wording in (question.mentor_question, question.mentee_question):
            for examples in _PARENTHETICAL.findall(wording or ""):
                terms.update(_clean_terms(examples))

    for respondent in respondents:
        for question in rows:
            terms.update(_clean_terms(respondent.responses.get(question.row, "")))

    vocabulary = tuple(sorted(terms))
    logger.info("controlled vocabulary holds %d terms", len(vocabulary))
    return vocabulary


def is_null_answer(text: str) -> bool:
    """Whether an answer means "nothing to avoid"."""
    return normalize(text).strip(" .!") in NULL_ANSWERS


def keyword_extractor(text: str, vocabulary: tuple[str, ...]) -> set[str]:
    """Find vocabulary terms stated outright in the answer."""
    haystack = normalize(text)
    return {
        term
        for term in vocabulary
        if re.search(rf"\b{re.escape(term)}\b", haystack)
    }


def extract_avoid_terms(
    question: Question,
    respondents: Iterable[Respondent],
    vocabulary: tuple[str, ...],
    extractor: Extractor = keyword_extractor,
) -> dict[str, set[str]]:
    """Resolve everyone's avoid answer, once per respondent rather than per pair."""
    extracted: dict[str, set[str]] = {}

    for respondent in respondents:
        answer = respondent.responses.get(question.row, "")
        if is_blank(answer) or is_null_answer(answer):
            continue

        terms = extractor(answer, vocabulary)
        if terms is None:
            # A failed extraction must not stop the run, so the answer is left
            # with no terms and nothing is blocked for this person.
            logger.warning(
                "could not extract avoid terms from %r", answer.strip()
            )
            continue

        if terms:
            extracted[respondent.key] = terms
            logger.info(
                "%s %s wants to avoid %s",
                respondent.side,
                respondent.key,
                ", ".join(sorted(terms)),
            )

    return extracted


def stated_terms_for_all(
    questions: list[Question],
    respondents: Iterable[Respondent],
    vocabulary: tuple[str, ...],
) -> dict[str, set[str]]:
    """The vocabulary terms each respondent said they work on.

    The question list and the vocabulary are the same for everyone, so both are
    resolved once rather than per respondent.
    """
    known = set(vocabulary)
    rows = [question.row for question in _vocabulary_questions(questions)]

    stated = {}
    for respondent in respondents:
        terms: set[str] = set()
        for row in rows:
            terms.update(_clean_terms(respondent.responses.get(row, "")))
        stated[respondent.key] = terms & known
    return stated


def blocked_cells(
    mentors: Iterable[Respondent],
    mentees: Iterable[Respondent],
    avoid_terms: dict[str, set[str]],
    stated: dict[str, set[str]],
) -> set[tuple[str, str]]:
    """The (mentor, mentee) keys to leave out of the assignment matrix.

    Checked in both directions: a mentee's preference rules a mentor out just
    as firmly as the reverse.
    """
    blocked = set()
    for mentor in mentors:
        mentor_avoids = avoid_terms.get(mentor.key, set())
        for mentee in mentees:
            mentee_avoids = avoid_terms.get(mentee.key, set())
            if mentor_avoids & stated.get(mentee.key, set()) or (
                mentee_avoids & stated.get(mentor.key, set())
            ):
                blocked.add((mentor.key, mentee.key))

    logger.info("avoid constraint blocked %d pairs", len(blocked))
    return blocked
