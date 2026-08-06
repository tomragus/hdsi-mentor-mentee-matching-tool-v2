"""Loading the two Google Forms exports and linking them to the questions database.

Column order is never assumed. Each database row finds its column by matching
question text on the normalized forms from `normalize`, so reordering a question
in the form cannot silently break the mapping.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from app.normalize import normalize
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


def find_timestamp_column(frame: pd.DataFrame) -> str | None:
    """Locate the submission-time column used to pick the latest duplicate."""
    return _header_lookup(frame).get(normalize(TIMESTAMP_HEADER))


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
