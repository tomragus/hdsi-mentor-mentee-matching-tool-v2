"""Scoring every pair, then assigning the cohort as a whole.

Two halves of one job. First each mentor is scored against each mentee: every
scored question contributes its points times its weight, less any write-in
penalty, over the maximum achievable on the questions both parties answered.
That ratio, not the raw total, is what ranking uses -- it stops pairs from
placing higher merely for having had more opportunities to earn points.

Then the assignment is solved globally rather than greedily. Taking the highest
pair, then the next, looks reasonable and is not: an early pair claims a mentor
a later mentee needed far more, and the cohort ends up worse overall.
"""

import logging
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from app.config import PERFECT_MATCH_POINTS, RANDOM_SEED
from app.embeddings import build_cache
from app.inputs import (
    MENTEE,
    MENTOR,
    ColumnLink,
    Respondent,
    ReviewFlag,
    build_respondents,
)
from app.questions import (
    ROLE_AVOID,
    ROLE_CHECKBOX,
    ROLE_LOCATION,
    ROLE_MULTIPLE_CHOICE,
    ROLE_SEMANTIC,
    Question,
)
from app.responses import Response, parse_responses
from app.scoring import (
    Cutoffs,
    LocationOffset,
    calibrate,
    resolve_offsets,
    score_location,
    score_options,
    score_semantic,
)
from app.writeins import penalty, resolve_write_ins
logger = logging.getLogger(__name__)

# Roles that earn points. The avoid row is excluded deliberately: it is a
# constraint on which pairings are allowed, not a measure of how well two
# people fit.
SCORED_ROLES = (ROLE_MULTIPLE_CHOICE, ROLE_CHECKBOX, ROLE_SEMANTIC, ROLE_LOCATION)


@dataclass(frozen=True)
class Participant:
    """A respondent together with their parsed answers. Plain immutable record."""

    respondent: Respondent
    answers: dict[int, Response]


@dataclass(frozen=True)
class ScoringContext:
    """Everything derived from the cohort as a whole, computed once per run.

    Plain immutable record. Cutoffs and location offsets both depend on who
    submitted this cycle, so they cannot be computed per pair.
    """

    questions: list[Question]
    cache: dict[str, np.ndarray]
    cutoffs: dict[int, Cutoffs]
    offsets: dict[str, LocationOffset]


@dataclass(frozen=True)
class QuestionScore:
    """One question's contribution to one pair. Plain immutable record."""

    row: int
    points: int
    weight: int
    penalty: int
    contribution: int
    maximum: int


@dataclass(frozen=True)
class PairScore:
    """One mentor/mentee combination's compatibility. Plain immutable record."""

    mentor_key: str
    mentee_key: str
    raw: int
    maximum: int
    # raw / maximum. Can be negative, since write-in penalties come off the raw
    # total and nothing clamps it.
    normalized: float
    question_scores: tuple[QuestionScore, ...]

    @property
    def percentage(self) -> float:
        return self.normalized * 100

    @property
    def scored_questions(self) -> int:
        """How many questions this score rests on, out of those both were asked."""
        return len(self.question_scores)


def _points_for(
    context: ScoringContext,
    question: Question,
    mentor: Participant,
    mentee: Participant,
) -> int | None:
    """Route one question to its scorer, or None if this pair cannot be scored."""
    if question.role == ROLE_LOCATION:
        return score_location(
            mentor.respondent.key, mentee.respondent.key, context.offsets
        )

    mentor_answer = mentor.answers.get(question.row)
    mentee_answer = mentee.answers.get(question.row)
    if mentor_answer is None or mentee_answer is None:
        return None

    if question.role == ROLE_SEMANTIC:
        return score_semantic(
            question, mentor_answer, mentee_answer, context.cache, context.cutoffs
        )
    return score_options(question, mentor_answer, mentee_answer)


def score_pair(
    context: ScoringContext, mentor: Participant, mentee: Participant
) -> PairScore:
    """Score one mentor against one mentee across every scored question."""
    scores: list[QuestionScore] = []

    for question in context.questions:
        if question.weight == 0 or question.role not in SCORED_ROLES:
            continue

        points = _points_for(context, question, mentor, mentee)
        if points is None:
            # Neither the points nor the maximum count, so an unanswered
            # question leaves the pair's ratio untouched.
            continue

        mentor_answer = mentor.answers.get(question.row)
        mentee_answer = mentee.answers.get(question.row)
        charged = (
            penalty(mentor_answer, mentee_answer)
            if mentor_answer and mentee_answer
            else 0
        )
        weighted = points * question.weight
        scores.append(
            QuestionScore(
                row=question.row,
                points=points,
                weight=question.weight,
                penalty=charged,
                # The penalty comes off after the multiplier, so it costs the
                # same on a weight-3 question as on a weight-1 one.
                contribution=weighted - charged,
                maximum=PERFECT_MATCH_POINTS * question.weight,
            )
        )

    raw = sum(score.contribution for score in scores)
    maximum = sum(score.maximum for score in scores)
    return PairScore(
        mentor_key=mentor.respondent.key,
        mentee_key=mentee.respondent.key,
        raw=raw,
        maximum=maximum,
        # A pair with nothing in common to score on has no ratio to speak of.
        normalized=raw / maximum if maximum else 0.0,
        question_scores=tuple(scores),
    )


def score_all(
    context: ScoringContext, mentors: list[Participant], mentees: list[Participant]
) -> dict[tuple[str, str], PairScore]:
    """Score the full mentor x mentee matrix, keyed by the pair's two keys."""
    scores = {
        (mentor.respondent.key, mentee.respondent.key): score_pair(
            context, mentor, mentee
        )
        for mentor in mentors
        for mentee in mentees
    }
    logger.info("scored %d mentor/mentee pairs", len(scores))
    return scores


def prepare(
    questions: list[Question],
    links: dict[int, ColumnLink],
    mentor_frame,
    mentee_frame,
) -> tuple[list[Participant], list[Participant], ScoringContext, list[ReviewFlag]]:
    """Run everything that happens before pair scoring, in order.

    Deduplicate, parse, embed once, resolve write-ins, then derive the
    cohort-wide values -- similarity cutoffs and time zone offsets -- that
    individual pair scores are measured against.
    """
    mentors, mentor_flags = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, mentee_flags = build_respondents(questions, links, mentee_frame, MENTEE)

    mentor_answers = [parse_responses(questions, person) for person in mentors]
    mentee_answers = [parse_responses(questions, person) for person in mentees]

    cache = build_cache(questions, mentor_answers + mentee_answers)
    mentor_answers = [
        resolve_write_ins(questions, MENTOR, answers, cache) for answers in mentor_answers
    ]
    mentee_answers = [
        resolve_write_ins(questions, MENTEE, answers, cache) for answers in mentee_answers
    ]

    cutoffs = calibrate(questions, mentor_answers, mentee_answers, cache)

    location = next((q for q in questions if q.role == ROLE_LOCATION), None)
    offsets, location_flags = (
        resolve_offsets(location, mentors + mentees) if location else ({}, [])
    )

    context = ScoringContext(
        questions=questions, cache=cache, cutoffs=cutoffs, offsets=offsets
    )
    participants = (
        [Participant(person, answers) for person, answers in zip(mentors, mentor_answers)],
        [Participant(person, answers) for person, answers in zip(mentees, mentee_answers)],
    )
    return (*participants, context, mentor_flags + mentee_flags + location_flags)


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
