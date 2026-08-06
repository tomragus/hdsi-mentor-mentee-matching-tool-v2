"""Scorers for the option-based questions.

Both work on option indices rather than option text, which is what lets the
feedback and mentoring-style rows score correctly despite wording their options
differently on each form.

A scorer returns 10, 5, or 0 on the unweighted scale, or None when the question
cannot be scored for this pair — either side left it blank, or their answer
resolved to no option at all. The pair-scoring step drops those questions from
both the score and the denominator.
"""

import logging

from app.config import NO_MATCH_POINTS
from app.questions import ROLE_CHECKBOX, ROLE_MULTIPLE_CHOICE, Question
from app.responses import Response

logger = logging.getLogger(__name__)


def score_multiple_choice(
    question: Question, mentor: Response, mentee: Response
) -> int | None:
    """Look the pair of chosen options up in this row's criteria table."""
    if not mentor.indices or not mentee.indices or not question.choice_scores:
        return None

    # A multiple choice answer is a single option; a resolved write-in leaves
    # exactly one index too.
    pair = (mentor.indices[0], mentee.indices[0])
    points = question.choice_scores.get(pair)
    if points is None:
        # The table is built from both orderings of every stated combination, so
        # a miss means the criteria simply do not mention this pairing.
        logger.warning(
            "row %d: no criteria for option pair %s, scoring it as no match",
            question.row,
            pair,
        )
        return NO_MATCH_POINTS
    return points


def score_checkbox(question: Question, mentor: Response, mentee: Response) -> int | None:
    """Score on how many options the two selected in common."""
    if not mentor.indices or not mentee.indices or not question.overlap_thresholds:
        return None

    overlap = len(set(mentor.indices) & set(mentee.indices))
    # Thresholds are held highest-first, so the first one met is the best one.
    for minimum, points in question.overlap_thresholds:
        if overlap >= minimum:
            return points
    return NO_MATCH_POINTS


def score_options(question: Question, mentor: Response, mentee: Response) -> int | None:
    """Score an option-based question, whichever kind it is."""
    if question.role == ROLE_MULTIPLE_CHOICE:
        return score_multiple_choice(question, mentor, mentee)
    if question.role == ROLE_CHECKBOX:
        return score_checkbox(question, mentor, mentee)
    return None
