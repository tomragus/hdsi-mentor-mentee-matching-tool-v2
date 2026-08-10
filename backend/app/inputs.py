"""Reading the two form exports and turning them into respondent records.

Two steps that always happen together. First every question in the database is
linked to a column in each export, by matching question text rather than by
position, so reordering a question in the form cannot silently change what is
compared to what. Then submissions are collapsed by email address, keeping the
most recent, leaving one record per person.

Original response text is kept verbatim: it is what gets shown when a
coordinator opens a match to check it.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from app.config import (
    DEFAULT_MENTOR_CAPACITY,
    EMAIL_QUESTION_KEYWORD,
    MENTEE_CAPACITY_QUESTION,
    NAME_QUESTION,
)
from app.normalize import is_blank, normalize
from app.questions import Question

TIMESTAMP_HEADER = "Timestamp"


class ExportLinkError(Exception):
    """An expected question has no matching column in its export.

    Carries every unresolved question rather than only the first, so a
    coordinator can fix the whole form in one pass.
    """

    def __init__(self, missing: list[tuple[str, int, str]]):
        self.missing = missing
        details = "\n".join(
            f"  - {side} export is missing the column for database row {row}: {text!r}"
            for side, row, text in missing
        )
        super().__init__(f"Could not link every question to a column:\n{details}")


@dataclass(frozen=True)
class ColumnLink:
    """Which export column answers a database row. Plain immutable record."""

    row: int
    mentor_column: str | None
    mentee_column: str | None


def read_export(source: str | Path | BinaryIO, name: str | None = None) -> pd.DataFrame:
    """Read one form export into a DataFrame, as CSV or Excel.

    Everything is read as text: response values are compared and embedded as
    strings, and letting pandas infer types would turn a graduation year into an
    integer and an all-numeric answer into a float.

    `name` supplies the filename when the source is a stream that has none, as
    an upload does, since the extension is what picks the reader.
    """
    name = str(name or getattr(source, "name", source)).lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(source, dtype=str)
    # utf-8-sig tolerates the byte-order mark Google Sheets sometimes writes.
    return pd.read_csv(source, dtype=str, encoding="utf-8-sig")


def _header_lookup(frame: pd.DataFrame) -> dict[str, str]:
    """Map each column's normalized header to its original header."""
    lookup: dict[str, str] = {}
    for column in frame.columns:
        lookup.setdefault(normalize(column), column)
    return lookup


def link_columns(
    questions: list[Question], mentor: pd.DataFrame, mentee: pd.DataFrame
) -> dict[int, ColumnLink]:
    """Pair each database row with its column in each export.

    Raises ExportLinkError naming every question that cannot be found, rather
    than skipping it — a skipped question would silently drop from scoring.
    """
    mentor_lookup = _header_lookup(mentor)
    mentee_lookup = _header_lookup(mentee)

    links: dict[int, ColumnLink] = {}
    missing: list[tuple[str, int, str]] = []

    for question in questions:
        columns: dict[str, str | None] = {"mentor": None, "mentee": None}
        for side, text, lookup in (
            ("mentor", question.mentor_question, mentor_lookup),
            ("mentee", question.mentee_question, mentee_lookup),
        ):
            # A question that is absent from one form has no column to find.
            if text is None:
                continue
            column = lookup.get(normalize(text))
            if column is None:
                missing.append((side, question.row, text))
            columns[side] = column

        links[question.row] = ColumnLink(
            row=question.row,
            mentor_column=columns["mentor"],
            mentee_column=columns["mentee"],
        )

    if missing:
        raise ExportLinkError(missing)
    return links


MENTOR = "mentor"
MENTEE = "mentee"

# Pulls the address out of a cell that carries extra text, such as
# "[not required] someone@ucsd.edu".
_EMAIL_PATTERN = re.compile(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+")

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}


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


def missing_email(respondent: Respondent) -> bool:
    """Whether this record had no readable address.

    The address is the identity key used to collapse repeat submissions, so
    without one a second submission from the same person becomes a second
    person. That is the one thing worth raising to a coordinator.
    """
    return not _extract_email(respondent.email)


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
    """Submission times, used to pick the latest of a person's duplicates."""
    column = _header_lookup(frame).get(normalize(TIMESTAMP_HEADER))
    if column is None:
        return pd.Series([pd.NaT] * len(frame))
    return pd.to_datetime(frame[column], errors="coerce", format="mixed")


def build_respondents(
    questions: list[Question],
    links: dict[int, ColumnLink],
    frame: pd.DataFrame,
    side: str,
) -> list[Respondent]:
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

    # Which column answers each question on this side, resolved once rather
    # than per row.
    columns = {
        question.row: _column_for(links[question.row], side) for question in questions
    }
    answered = {row: column for row, column in columns.items() if column is not None}
    email_column = columns.get(email_question.row) if email_question else None
    name_column = columns.get(name_question.row) if name_question else None
    capacity_column = columns.get(capacity_question.row) if capacity_question else None

    submitted = _timestamps(frame)
    # Insertion order is preserved, so respondents keep their form order.
    latest: dict[str, Respondent] = {}

    for position in range(len(frame)):
        email = _cell(frame, position, email_column)
        # Without an address this submission cannot be matched against any
        # other, so it gets a key of its own. `missing_email` finds these again.
        key = _extract_email(email) or f"{side}-row-{position + 1}"

        name = _cell(frame, position, name_column)

        capacity = DEFAULT_MENTOR_CAPACITY
        if capacity_column is not None:
            capacity = _parse_capacity(_cell(frame, position, capacity_column))

        responses = {
            row: _cell(frame, position, column) for row, column in answered.items()
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

    return list(latest.values())


def _is_newer(candidate: Respondent, existing: Respondent) -> bool:
    """Whether a later submission should replace the one already kept.

    A submission with no readable timestamp still wins on a tie, since rows
    appear in the export in submission order.
    """
    if candidate.submitted_at is None or existing.submitted_at is None:
        return True
    return candidate.submitted_at >= existing.submitted_at
