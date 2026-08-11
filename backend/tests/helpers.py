"""Paths and stand-in records the test files share.

Kept apart from conftest.py, which holds only fixtures, so the builders below
can be imported by name.
"""

from pathlib import Path

import numpy as np

from app.config import normalize
from app.inputs import (
    KIND_BLANK,
    KIND_CHECKBOX,
    KIND_CHOICE,
    KIND_TEXT,
    MENTEE,
    MENTOR,
    ROLE_CHECKBOX,
    Option,
    Question,
    Respondent,
    Response,
    build_respondents,
    link_columns,
    read_export,
)
from app.matching import PairScore, Participant

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parents[2]
DATABASE = ROOT / "Mentee_Mentor Questions Database.csv"
REAL_MENTOR = FIXTURES / "mentor_responses.csv"
REAL_MENTEE = FIXTURES / "mentee_responses.csv"

# Cohort A is the oversubscribed one. The waitlist and capacity tests need
# mentees to outnumber mentor slots, which cohort B deliberately does not do.
SYNTHETIC_MENTOR = ROOT / "Synthetic A - Alumni Mentor Questionnaire (Responses).csv"
SYNTHETIC_MENTEE = ROOT / "Synthetic A - Student Mentee Questionnaire (Responses).csv"

# Database rows the tests refer to by name.
COMMITMENT_ROW, COMMUNICATION_ROW = 1, 2
FEEDBACK_ROW, STYLE_ROW = 9, 11


def cohort(
    mentor_path: Path, mentee_path: Path, questions: list[Question]
) -> tuple[list[Respondent], list[Respondent]]:
    """Both sides of one cohort, deduplicated but not yet parsed."""
    mentor_frame, mentee_frame = read_export(mentor_path), read_export(mentee_path)
    links = link_columns(questions, mentor_frame, mentee_frame)
    return (
        build_respondents(questions, links, mentor_frame, MENTOR),
        build_respondents(questions, links, mentee_frame, MENTEE),
    )


def stand_in(
    row: int = 99,
    role: str = ROLE_CHECKBOX,
    weight: int = 1,
    options: tuple | list = (),
    percentiles: tuple[int, int] = (85, 50),
    choice_scores: dict | None = None,
    overlap_thresholds: tuple | None = (),
) -> Question:
    """A question built by hand, for cases the real database has no example of.

    `options` holds either plain text or (text, is_write_in) pairs, numbered
    from one and shared by both sides.
    """
    listed = tuple(
        Option(
            index=index,
            text=item[0] if isinstance(item, tuple) else item,
            is_write_in=item[1] if isinstance(item, tuple) else False,
        )
        for index, item in enumerate(options, start=1)
    )
    return Question(
        row=row,
        role=role,
        weight=weight,
        mentor_question=f"stand-in {row}",
        mentee_question=f"stand-in {row}",
        mentor_required=False,
        mentee_required=False,
        mentor_options=listed,
        mentee_options=listed,
        percentiles=percentiles,
        choice_scores=choice_scores,
        overlap_thresholds=overlap_thresholds,
    )


def choice(row: int, index: int, write_in: bool = False) -> Response:
    return Response(
        row=row,
        kind=KIND_CHOICE,
        text=f"option {index}",
        indices=(index,),
        write_ins=("something typed in",) if write_in else (),
    )


def checkbox(row: int, *indices: int) -> Response:
    return Response(row, KIND_CHECKBOX, "x", tuple(indices), ())


def blank(row: int) -> Response:
    return Response(row, KIND_BLANK, "", (), ())


def written(row: int, value: str) -> Response:
    return Response(row, KIND_TEXT, value, (), ())


def respondent(key: str, side: str, capacity: int = 1) -> Respondent:
    """A respondent identified only by key, since blocking works on keys."""
    return Respondent(
        key=key,
        side=side,
        name=key.upper(),
        email=key,
        capacity=capacity,
        submitted_at=None,
        responses={},
    )


def participant(
    key: str, side: str, answers: dict[int, Response] | None = None, capacity: int = 1
) -> Participant:
    return Participant(respondent(key, side, capacity), answers or {})


def score_table(
    values: dict[tuple[str, str], float],
) -> dict[tuple[str, str], PairScore]:
    """A score table built straight from normalized values."""
    return {
        pair: PairScore(
            mentor_key=pair[0],
            mentee_key=pair[1],
            raw=int(value * 100),
            maximum=100,
            normalized=value,
            question_scores=(),
        )
        for pair, value in values.items()
    }


def cache_from(vectors: dict[str, list[float]]) -> dict[str, np.ndarray]:
    """A cache of unit vectors built by hand, so similarities are exact."""
    return {
        normalize(text): np.array(values, dtype=float) / np.linalg.norm(values)
        for text, values in vectors.items()
    }


def spread_cache(labels: list[str]) -> dict[str, np.ndarray]:
    """Unit vectors spaced evenly around a quarter circle, for known cosines."""
    angles = np.linspace(0, np.pi / 2, len(labels))
    return {
        normalize(label): np.array([np.cos(angle), np.sin(angle)])
        for label, angle in zip(labels, angles)
    }
