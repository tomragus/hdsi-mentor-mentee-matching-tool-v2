"""Resolving write-in answers to the option they most resemble.

Google Forms exports an "Other" answer as the text the respondent typed, so a
write-in reaches us as a string that matches no listed option. Rather than
discard it, each one is compared against that question's options by cosine
similarity and treated as whichever it is closest to.

The original text stays on the response. It is what a coordinator sees, and its
presence is what triggers the write-in penalty during pair scoring.
"""

import logging
from dataclasses import replace

import numpy as np

from app.config import WRITE_IN_PENALTY
from app.embeddings import similarity
from app.questions import ROLE_CHECKBOX, ROLE_MULTIPLE_CHOICE, Question
from app.inputs import MENTOR
from app.responses import Response

logger = logging.getLogger(__name__)

_RESOLVABLE = (ROLE_MULTIPLE_CHOICE, ROLE_CHECKBOX)


def nearest_option(
    question: Question, side: str, text: str, cache: dict[str, np.ndarray]
) -> int | None:
    """The index of the listed option a write-in most resembles."""
    options = question.mentor_options if side == MENTOR else question.mentee_options
    # The "Other" option is skipped: its text is the literal word, which tells
    # us nothing about what the respondent wrote.
    listed = [option for option in options if not option.is_write_in and option.text]
    if not listed:
        return None

    best_index = None
    best_score = float("-inf")
    # Options are visited in index order and ties keep the earlier one, so the
    # result does not depend on iteration order.
    for option in sorted(listed, key=lambda option: option.index):
        score = similarity(cache, text, option.text)
        if score > best_score:
            best_index, best_score = option.index, score
    return best_index


def resolve_response(
    question: Question, side: str, response: Response, cache: dict[str, np.ndarray]
) -> Response:
    """Add the nearest-option index for each write-in on one answer."""
    if question.role not in _RESOLVABLE or not response.write_ins:
        return response

    indices = list(response.indices)
    for text in response.write_ins:
        index = nearest_option(question, side, text, cache)
        if index is None:
            continue
        logger.info(
            "row %d: write-in %r resolved to option %d", question.row, text, index
        )
        if index not in indices:
            indices.append(index)

    return replace(response, indices=tuple(indices))


def resolve_write_ins(
    questions: list[Question],
    side: str,
    answers: dict[int, Response],
    cache: dict[str, np.ndarray],
) -> dict[int, Response]:
    """Resolve every write-in in one respondent's parsed answers."""
    by_row = {question.row: question for question in questions}
    return {
        row: resolve_response(by_row[row], side, response, cache)
        for row, response in answers.items()
        if row in by_row
    }


def penalty(mentor_response: Response, mentee_response: Response) -> int:
    """Points removed from a question once both sides' answers are weighted.

    A flat charge: one write-in costs the same as two, since the penalty is for
    the answer being inferred rather than stated.
    """
    if mentor_response.write_ins or mentee_response.write_ins:
        return WRITE_IN_PENALTY
    return 0
