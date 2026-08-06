"""Solving the whole cohort at once rather than picking matches greedily.

Taking the highest-scoring pair, then the next, and so on looks reasonable and
is not: an early pair claims a mentor that a later mentee needed far more, and
the cohort as a whole ends up worse. So the assignment is solved globally,
maximizing total compatibility across every pairing at the same time.

The matrix is padded to a square. A mentor who can take two mentees appears as
two columns. Mentees who cannot be placed fall onto dummy columns scored 0,
which is what leaves them on the waitlist, and surplus mentor slots fall onto
dummy rows.
"""

import logging
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from app.config import RANDOM_SEED
from app.pairs import PairScore, Participant

logger = logging.getLogger(__name__)

# Low enough that the solver will always prefer waitlisting a mentee, but
# finite, so a fully blocked mentee cannot make the problem unsolvable.
BLOCKED_SCORE = -1.0e6
# High enough to override every real score, so a pinned pairing survives.
PINNED_SCORE = 1.0e6
# Smaller than any meaningful difference between two scores, so it only decides
# exact ties.
TIE_BREAK_RANGE = 1.0e-9


@dataclass(frozen=True)
class Slot:
    """One opening with one mentor. Plain immutable record.

    A mentor who takes two mentees has two of these, so the solver can fill
    them independently.
    """

    mentor_key: str
    position: int


@dataclass(frozen=True)
class Assignment:
    """One mentor paired with one mentee. Plain immutable record."""

    mentor_key: str
    mentee_key: str
    score: PairScore


@dataclass(frozen=True)
class Solution:
    """The result of one solve. Plain immutable record."""

    assignments: tuple[Assignment, ...]
    # Mentee keys that ended up on a dummy column.
    unassigned: tuple[str, ...]
    unfilled_slots: int


def build_slots(mentors: list[Participant]) -> list[Slot]:
    """One slot per mentee a mentor said they could take."""
    return [
        Slot(mentor_key=mentor.respondent.key, position=position)
        for mentor in mentors
        for position in range(max(1, mentor.respondent.capacity))
    ]


def build_matrix(
    mentees: list[Participant],
    slots: list[Slot],
    scores: dict[tuple[str, str], PairScore],
    blocked: set[tuple[str, str]],
    pinned: set[tuple[str, str]],
    forbidden: set[tuple[str, str]],
) -> np.ndarray:
    """Build the square score matrix the solver works on.

    Rows are mentees and columns are mentor slots, both padded with dummies so
    the matrix is square and every row can be assigned somewhere.
    """
    size = max(len(mentees), len(slots))
    # Dummy rows and columns stay at zero: an unfilled slot and a waitlisted
    # mentee are both worth nothing rather than negative.
    matrix = np.zeros((size, size), dtype=float)

    for row, mentee in enumerate(mentees):
        mentee_key = mentee.respondent.key
        for column, slot in enumerate(slots):
            pair = (slot.mentor_key, mentee_key)
            if pair in pinned:
                matrix[row, column] = PINNED_SCORE
            elif pair in blocked or pair in forbidden:
                matrix[row, column] = BLOCKED_SCORE
            else:
                score = scores.get(pair)
                matrix[row, column] = score.normalized if score else BLOCKED_SCORE

    # Seeded jitter, so two identical scores resolve the same way on every run
    # of the same inputs rather than however the solver happens to break ties.
    rng = np.random.default_rng(RANDOM_SEED)
    return matrix + rng.uniform(0, TIE_BREAK_RANGE, size=matrix.shape)


def solve(
    mentors: list[Participant],
    mentees: list[Participant],
    scores: dict[tuple[str, str], PairScore],
    blocked: set[tuple[str, str]] | None = None,
    pinned: set[tuple[str, str]] | None = None,
    forbidden: set[tuple[str, str]] | None = None,
) -> Solution:
    """Assign mentees to mentor slots, maximizing total compatibility."""
    slots = build_slots(mentors)
    if not slots or not mentees:
        return Solution(
            assignments=(),
            unassigned=tuple(m.respondent.key for m in mentees),
            unfilled_slots=len(slots),
        )

    matrix = build_matrix(
        mentees, slots, scores, blocked or set(), pinned or set(), forbidden or set()
    )
    rows, columns = linear_sum_assignment(matrix, maximize=True)

    assignments = []
    assigned_mentees = set()
    filled_slots = 0

    for row, column in zip(rows, columns):
        if row >= len(mentees) or column >= len(slots):
            # One side of this pairing is padding, so nothing was matched.
            continue
        mentee_key = mentees[row].respondent.key
        mentor_key = slots[column].mentor_key
        if matrix[row, column] <= BLOCKED_SCORE / 2:
            # Only reachable when a mentee has no unblocked slot and no dummy
            # column to fall onto, which the padding normally prevents.
            logger.warning("no permitted mentor for mentee %s", mentee_key)
            continue

        assignments.append(
            Assignment(
                mentor_key=mentor_key,
                mentee_key=mentee_key,
                score=scores[(mentor_key, mentee_key)],
            )
        )
        assigned_mentees.add(mentee_key)
        filled_slots += 1

    assignments.sort(key=lambda item: item.score.normalized, reverse=True)
    unassigned = tuple(
        mentee.respondent.key
        for mentee in mentees
        if mentee.respondent.key not in assigned_mentees
    )

    logger.info(
        "assigned %d of %d mentees across %d slots",
        len(assignments),
        len(mentees),
        len(slots),
    )
    return Solution(
        assignments=tuple(assignments),
        unassigned=unassigned,
        unfilled_slots=len(slots) - filled_slots,
    )
