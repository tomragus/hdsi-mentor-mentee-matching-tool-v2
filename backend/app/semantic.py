"""Semantic scoring, calibrated per question against the current cohort.

Cosine values from this model are not on an absolute scale, and their range
shifts a lot by question: one where everybody answers with the same handful of
tools produces uniformly high similarities, while an open-ended one produces
uniformly low ones. A single fixed cutoff would therefore make some questions
award 10 to everyone and others 0 to everyone, contributing no variance and
quietly nullifying the weight column.

So each question's cutoffs are derived from its own distribution: the value at
its upper percentile becomes the 10-point cutoff and the value at its lower
percentile the 5-point cutoff. Raw cosines still differ across questions, but
the meaning is constant, since every question awards full points to the same
top fraction of pairs.

Scores are therefore relative to this cohort. A 10 means "top 15% of pairs on
this question in this run", not "these two answers are objectively similar" --
which is the right frame, since the goal is ranking within a fixed pool.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from app.config import GOOD_MATCH_POINTS, NO_MATCH_POINTS, PERFECT_MATCH_POINTS
from app.embeddings import similarity
from app.questions import ROLE_SEMANTIC, Question
from app.responses import KIND_BLANK, Response

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cutoffs:
    """One question's derived similarity thresholds. Plain immutable record."""

    row: int
    percentiles: tuple[int, int]
    upper: float
    lower: float
    pair_count: int


def _answered(answers: dict[int, Response], row: int) -> Response | None:
    response = answers.get(row)
    if response is None or response.kind == KIND_BLANK or not response.text.strip():
        return None
    return response


def similarities(
    question: Question,
    mentor_answers: Iterable[dict[int, Response]],
    mentee_answers: Iterable[dict[int, Response]],
    cache: dict[str, np.ndarray],
) -> list[float]:
    """Cosine similarity for every pair where both sides answered this question."""
    mentors = [a for a in mentor_answers if _answered(a, question.row)]
    mentees = [a for a in mentee_answers if _answered(a, question.row)]
    return [
        similarity(cache, mentor[question.row].text, mentee[question.row].text)
        for mentor in mentors
        for mentee in mentees
    ]


def calibrate(
    questions: list[Question],
    mentor_answers: list[dict[int, Response]],
    mentee_answers: list[dict[int, Response]],
    cache: dict[str, np.ndarray],
) -> dict[int, Cutoffs]:
    """Derive each semantic question's cutoffs from this cohort's own scores."""
    derived: dict[int, Cutoffs] = {}

    for question in questions:
        if question.role != ROLE_SEMANTIC:
            continue

        values = similarities(question, mentor_answers, mentee_answers, cache)
        if not values:
            # Nobody on one side answered, so there is no distribution to read
            # cutoffs from and the question drops out for every pair.
            logger.warning("row %d: no answered pairs, question not scored", question.row)
            continue

        upper_pct, lower_pct = question.percentiles
        upper, lower = np.percentile(values, [upper_pct, lower_pct])
        derived[question.row] = Cutoffs(
            row=question.row,
            percentiles=question.percentiles,
            upper=float(upper),
            lower=float(lower),
            pair_count=len(values),
        )
        logger.info(
            "row %d: %d/%d percentile cutoffs are %.3f/%.3f over %d pairs",
            question.row,
            upper_pct,
            lower_pct,
            upper,
            lower,
            len(values),
        )

    return derived


def score_semantic(
    question: Question,
    mentor: Response,
    mentee: Response,
    cache: dict[str, np.ndarray],
    cutoffs: dict[int, Cutoffs],
) -> int | None:
    """Score one pair on one semantic question against its derived cutoffs."""
    derived = cutoffs.get(question.row)
    if derived is None:
        return None
    if mentor.kind == KIND_BLANK or mentee.kind == KIND_BLANK:
        return None
    if not mentor.text.strip() or not mentee.text.strip():
        return None

    value = similarity(cache, mentor.text, mentee.text)
    if value >= derived.upper:
        return PERFECT_MATCH_POINTS
    if value >= derived.lower:
        return GOOD_MATCH_POINTS
    return NO_MATCH_POINTS
