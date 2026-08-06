"""Deduplication and per-respondent records.

Respondents may submit a form more than once, so submissions are collapsed by
email address, keeping the most recent. What survives is one record per person
holding their identity, their capacity, and their answer to every linked
question.

Original response text is kept verbatim: it is what gets shown when a
coordinator opens a match to check it.
"""

import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.config import (
    DEFAULT_MENTOR_CAPACITY,
    EMAIL_QUESTION_KEYWORD,
    MENTEE_CAPACITY_QUESTION,
    NAME_QUESTION,
)
from app.exports import ColumnLink, find_timestamp_column
from app.normalize import is_blank, normalize
from app.questions import Question

MENTOR = "mentor"
MENTEE = "mentee"

# Pulls the address out of a cell that carries extra text, such as
# "[not required] someone@ucsd.edu".
_EMAIL_PATTERN = re.compile(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+")

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}


@dataclass(frozen=True)
class ReviewFlag:
    """Something a coordinator should look at by hand. Plain immutable record."""

    side: str
    respondent_key: str
    reason: str


@dataclass(frozen=True)
class Respondent:
    """One deduplicated survey respondent. Plain immutable record."""

    key: str
    side: str
    name: str
    email: str
    capacity: int
    submitted_at: datetime | None
    # question row -> the respondent's original answer text
    responses: dict[int, str]


def _question_text(question: Question, side: str) -> str | None:
    return question.mentor_question if side == MENTOR else question.mentee_question


def _column_for(link: ColumnLink, side: str) -> str | None:
    return link.mentor_column if side == MENTOR else link.mentee_column


def _find_question(
    questions: list[Question], side: str, matches
) -> Question | None:
    for question in questions:
        text = _question_text(question, side)
        if text is not None and matches(normalize(text)):
            return question
    return None


def _extract_email(raw: object) -> str:
    """Return the normalized address in a cell, or "" if there isn't one."""
    if not isinstance(raw, str):
        return ""
    found = _EMAIL_PATTERN.search(raw)
    return normalize(found.group(0)) if found else ""


def _parse_capacity(raw: object) -> int:
    """Read a mentor's stated number of mentees from their answer."""
    text = normalize(raw)
    if not text:
        return DEFAULT_MENTOR_CAPACITY
    first = text.split()[0].strip("().,;:")
    if first.isdigit():
        return max(1, int(first))
    return _NUMBER_WORDS.get(first, DEFAULT_MENTOR_CAPACITY)


def _cell(frame: pd.DataFrame, position: int, column: str | None) -> str:
    """Read one cell as display text, with blanks becoming ""."""
    if column is None:
        return ""
    value = frame.iloc[position][column]
    return "" if is_blank(value) else str(value).strip()


def _timestamps(frame: pd.DataFrame) -> pd.Series:
    column = find_timestamp_column(frame)
    if column is None:
        return pd.Series([pd.NaT] * len(frame))
    return pd.to_datetime(frame[column], errors="coerce", format="mixed")


def build_respondents(
    questions: list[Question],
    links: dict[int, ColumnLink],
    frame: pd.DataFrame,
    side: str,
) -> tuple[list[Respondent], list[ReviewFlag]]:
    """Build one deduplicated record per respondent on one side of the match."""
    name_question = _find_question(
        questions, side, lambda text: text == normalize(NAME_QUESTION)
    )
    email_question = _find_question(
        questions, side, lambda text: normalize(EMAIL_QUESTION_KEYWORD) in text
    )
    capacity_question = _find_question(
        questions, side, lambda text: text == normalize(MENTEE_CAPACITY_QUESTION)
    )

    submitted = _timestamps(frame)
    flags: list[ReviewFlag] = []
    # Insertion order is preserved, so respondents keep their form order.
    latest: dict[str, Respondent] = {}

    for position in range(len(frame)):
        email_column = (
            _column_for(links[email_question.row], side) if email_question else None
        )
        email = _cell(frame, position, email_column)
        key = _extract_email(email)

        if not key:
            # Without an address this submission cannot be matched against any
            # other, so it gets a key of its own and is raised for review.
            key = f"{side}-row-{position + 1}"
            flags.append(
                ReviewFlag(
                    side=side,
                    respondent_key=key,
                    reason="no email address given, so duplicate submissions "
                    "cannot be detected for this respondent",
                )
            )

        name_column = (
            _column_for(links[name_question.row], side) if name_question else None
        )
        name = _cell(frame, position, name_column)

        capacity = DEFAULT_MENTOR_CAPACITY
        if capacity_question is not None:
            capacity = _parse_capacity(
                _cell(frame, position, _column_for(links[capacity_question.row], side))
            )

        responses = {
            question.row: _cell(frame, position, _column_for(links[question.row], side))
            for question in questions
            if _column_for(links[question.row], side) is not None
        }

        timestamp = submitted.iloc[position]
        record = Respondent(
            key=key,
            side=side,
            # Falling back to the address keeps every row identifiable on the
            # leaderboard even when the name is blank.
            name=name or email or key,
            email=email,
            capacity=capacity,
            submitted_at=None if pd.isna(timestamp) else timestamp.to_pydatetime(),
            responses=responses,
        )

        previous = latest.get(key)
        if previous is None or _is_newer(record, previous):
            latest[key] = record

    return list(latest.values()), flags


def _is_newer(candidate: Respondent, existing: Respondent) -> bool:
    """Whether a later submission should replace the one already kept.

    A submission with no readable timestamp still wins on a tie, since rows
    appear in the export in submission order.
    """
    if candidate.submitted_at is None or existing.submitted_at is None:
        return True
    return candidate.submitted_at >= existing.submitted_at
