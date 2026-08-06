"""Turning every mentor/mentee combination into one comparable number.

Each scored question contributes its points times its weight, less any write-in
penalty. A question either side left blank is dropped from both the total and
the denominator, so skipping an optional question costs a pair nothing rather
than lowering the ceiling they are measured against.

The result is raw points over the maximum achievable on the questions both
parties actually answered. That ratio, not the raw total, is what the
leaderboard ranks on: it stops pairs from placing higher merely for having had
more opportunities to earn points.
"""

import logging
from dataclasses import dataclass

import numpy as np

from app.config import PERFECT_MATCH_POINTS
from app.embeddings import build_cache
from app.exports import ColumnLink
from app.location import LocationOffset, resolve_offsets, score_location
from app.questions import (
    ROLE_AVOID,
    ROLE_CHECKBOX,
    ROLE_LOCATION,
    ROLE_MULTIPLE_CHOICE,
    ROLE_SEMANTIC,
    Question,
)
from app.respondents import MENTEE, MENTOR, Respondent, ReviewFlag, build_respondents
from app.responses import Response, parse_responses
from app.scorers import score_options
from app.semantic import Cutoffs, calibrate, score_semantic
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
