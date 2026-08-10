"""Everything that turns files into structured, comparable answers.

Four stages, in the order they run:

1. The questions database -- one record per row, defining what each question is
   and how it scores. Everything downstream keys off these records: the mentor
   and mentee questions are paired by row, and their options align by index.
2. The two form exports -- linked to those questions by matching question text
   rather than by column position, then collapsed by email address so there is
   one record per person. Original response text is kept verbatim, since it is
   what a coordinator sees when checking a match.
3. Parsing -- each answer cell resolved once against its question's option list,
   so everything after this compares option indices rather than text. Anything
   matching no listed option is carried forward as a write-in.
4. Embedding and write-in resolution -- every distinct string embedded exactly
   once as a unit vector, so scoring is a dot product; then each write-in is
   resolved to the listed option it most resembles. The original text stays on
   the response, and its presence is what triggers the write-in penalty later.
"""

import csv
import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pandas as pd

from app.config import (
    AVOID_QUESTION_PREFIX,
    DEFAULT_MENTOR_CAPACITY,
    DEFAULT_PERCENTILES,
    DISPLAY_ORDER,
    EMAIL_QUESTION_KEYWORD,
    EMBEDDING_MODEL,
    LOCATION_QUESTION_PREFIX,
    MENTEE_CAPACITY_QUESTION,
    NAME_QUESTION,
    WRITE_IN_PENALTY,
    is_blank,
    normalize,
)

logger = logging.getLogger(__name__)


# --- 1. the questions database ------------------------------------------------

# How a row is scored. Routing happens once, at load time.
ROLE_UNSCORED = "unscored"
ROLE_MULTIPLE_CHOICE = "multiple_choice"
ROLE_CHECKBOX = "checkbox"
ROLE_SEMANTIC = "semantic"
ROLE_LOCATION = "location"
ROLE_AVOID = "avoid"

NATURAL_LANGUAGE_MARKER = "{natural language input}"

_OPTION_NUMBER = re.compile(r"^\s*(\d+)\s*[.)]\s*(.*)$", re.DOTALL)
_SCORE_LABEL = re.compile(r"\b(10|5|0)\s*:")
_LEADING_INT = re.compile(r"(\d+)")


@dataclass(frozen=True)
class Option:
    """A single response option. Plain immutable record, no behavior."""

    index: int
    text: str
    is_write_in: bool


@dataclass(frozen=True)
class Question:
    """One row of the questions database. Plain immutable record, no behavior."""

    row: int
    role: str
    weight: int
    mentor_question: str
    mentee_question: str | None
    # Read by the synthetic-data generator to decide how often to leave an
    # answer blank; the app itself does not enforce them.
    mentor_required: bool
    mentee_required: bool
    mentor_options: tuple[Option, ...]
    mentee_options: tuple[Option, ...]
    percentiles: tuple[int, int]
    # (mentor_index, mentee_index) -> points, for multiple choice rows.
    choice_scores: dict[tuple[int, int], int] | None
    # (minimum_overlap, points), highest threshold first, for checkbox rows.
    overlap_thresholds: tuple[tuple[int, int], ...] | None


def _strip_braces(cell: str) -> str:
    """Drop the outer { } wrapper, leaving any nested braces intact."""
    text = cell.strip()
    if text.startswith("{") and text.endswith("}"):
        return text[1:-1].strip()
    return text


def _is_na(cell: str) -> bool:
    return is_blank(cell) or normalize(cell) == "na"


def _parse_required(cell: str) -> tuple[bool, bool]:
    """Return (required_for_mentor, required_for_mentee).

    Most rows are a plain Yes/No that applies to both sides, but one row reads
    "Yes for mentor No for mentee".
    """
    text = normalize(cell)
    if "mentor" in text or "mentee" in text:
        return "yes for mentor" in text, "yes for mentee" in text
    required = text.startswith("yes")
    return required, required


def _parse_percentiles(cell: str) -> tuple[int, int]:
    """Parse an upper/lower percentile pair such as "85/50"."""
    if _is_na(cell):
        return DEFAULT_PERCENTILES
    parts = [p.strip() for p in cell.split("/")]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"could not read percentile cutoffs from {cell!r}")
    upper, lower = int(parts[0]), int(parts[1])
    if not 0 <= lower < upper <= 100:
        raise ValueError(f"percentile cutoffs out of order or out of range: {cell!r}")
    return upper, lower


def _parse_options(cell: str) -> tuple[Option, ...]:
    """Split an option list on semicolons only.

    Never on commas: at least one option's own text contains a comma, so comma
    splitting would silently break that option in half.
    """
    if _is_na(cell) or normalize(cell) == normalize(NATURAL_LANGUAGE_MARKER):
        return ()

    options = []
    for position, chunk in enumerate(_strip_braces(cell).split(";"), start=1):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _OPTION_NUMBER.match(chunk)
        if match:
            index, text = int(match.group(1)), match.group(2).strip()
        else:
            index, text = position, chunk
        is_write_in = NATURAL_LANGUAGE_MARKER in text.casefold()
        if is_write_in:
            # "Other {natural language input}" displays as "Other".
            text = re.sub(
                re.escape(NATURAL_LANGUAGE_MARKER), "", text, flags=re.IGNORECASE
            ).strip()
        options.append(Option(index=index, text=text, is_write_in=is_write_in))
    return tuple(options)


def _option_index_lookup(
    mentor_options: tuple[Option, ...], mentee_options: tuple[Option, ...]
) -> dict[str, set[int]]:
    """Map normalized option text to the index it refers to.

    Both sides feed one lookup because the criteria column sometimes quotes the
    mentor wording and sometimes the mentee wording for the same index.
    """
    lookup: dict[str, set[int]] = {}
    for options in (mentor_options, mentee_options):
        for option in options:
            key = normalize(option.text)
            if key:
                lookup.setdefault(key, set()).add(option.index)
    return lookup


def _lookup_side(text: str, lookup: dict[str, set[int]]) -> set[int]:
    """Resolve one side of a combination to option indices.

    A side may offer both wordings separated by "|", as in
    "For it to be direct and concise | To be direct and concise".
    """
    indices: set[int] = set()
    for alternative in text.split("|"):
        indices |= lookup.get(normalize(alternative), set())
    return indices


def _split_shared_chunk(chunk: str, lookup: dict[str, set[int]]) -> tuple[str, str]:
    """Split "<end of one combination>, <start of the next>" at the right comma.

    Option text can itself contain commas, so the correct split is the earliest
    one where both halves resolve to real options.
    """
    for position, character in enumerate(chunk):
        if character != ",":
            continue
        left, right = chunk[:position], chunk[position + 1 :]
        if _lookup_side(left, lookup) and _lookup_side(right, lookup):
            return left, right
    raise ValueError(f"could not split combinations in {chunk!r}")


def _parse_combinations(
    segment: str, lookup: dict[str, set[int]]
) -> list[tuple[str, str]]:
    """Split a score segment into (left side, right side) text pairs."""
    sides = segment.split("&")
    if len(sides) < 2:
        return []

    combinations = []
    left = sides[0]
    for chunk in sides[1:-1]:
        right, next_left = _split_shared_chunk(chunk, lookup)
        combinations.append((left, right))
        left = next_left
    combinations.append((left, sides[-1]))
    return combinations


def _score_segments(criteria: str) -> dict[int, str]:
    """Split the criteria cell into its 10 / 5 / 0 segments."""
    inner = _strip_braces(criteria)
    labels = list(_SCORE_LABEL.finditer(inner))
    segments = {}
    for position, label in enumerate(labels):
        end = labels[position + 1].start() if position + 1 < len(labels) else len(inner)
        # Trailing punctuation absorbs a stray ":" used in place of ";".
        segments[int(label.group(1))] = inner[label.end() : end].strip().strip(";:,")
    return segments


def _parse_choice_scores(
    criteria: str, mentor_options: tuple[Option, ...], mentee_options: tuple[Option, ...]
) -> dict[tuple[int, int], int]:
    """Build the (mentor index, mentee index) -> points table.

    Combinations count in either order, so each one contributes both
    orderings.
    """
    lookup = _option_index_lookup(mentor_options, mentee_options)
    scores: dict[tuple[int, int], int] = {}
    segments = _score_segments(criteria)

    for points in (10, 5, 0):
        segment = segments.get(points)
        if not segment:
            continue
        for left_text, right_text in _parse_combinations(segment, lookup):
            left = _lookup_side(left_text, lookup)
            right = _lookup_side(right_text, lookup)
            if not left or not right:
                unresolved = left_text if not left else right_text
                raise ValueError(
                    f"criteria mention an option that is not in the option list: "
                    f"{unresolved.strip()!r}"
                )
            for a in left:
                for b in right:
                    # Higher score buckets are processed first and win ties.
                    scores.setdefault((a, b), points)
                    scores.setdefault((b, a), points)
    return scores


def _parse_overlap_thresholds(criteria: str) -> tuple[tuple[int, int], ...]:
    """Build the (minimum overlap, points) table for a checkbox row."""
    thresholds = []
    for points, segment in _score_segments(criteria).items():
        match = _LEADING_INT.search(segment)
        minimum = int(match.group(1)) if match else 0
        thresholds.append((minimum, points))
    return tuple(sorted(thresholds, reverse=True))


def _route(
    response_type: str, weight: int, mentor_question: str, is_natural_language: bool
) -> str:
    """Decide which scorer handles this row."""
    question = normalize(mentor_question)
    if question.startswith(normalize(LOCATION_QUESTION_PREFIX)):
        return ROLE_LOCATION
    if question.startswith(normalize(AVOID_QUESTION_PREFIX)):
        return ROLE_AVOID
    if weight == 0:
        return ROLE_UNSCORED
    if response_type == "multiple choice":
        return ROLE_MULTIPLE_CHOICE
    if response_type == "check box":
        return ROLE_CHECKBOX
    if is_natural_language:
        return ROLE_SEMANTIC
    return ROLE_UNSCORED


def for_display(questions: list[Question]) -> list[Question]:
    """Questions in the order a coordinator reads them, not database order.

    A row missing from DISPLAY_ORDER sorts to the end rather than vanishing, so
    adding a question to the database can never silently hide it.
    """
    position = {row: index for index, row in enumerate(DISPLAY_ORDER)}
    return sorted(questions, key=lambda q: position.get(q.row, len(position)))


def load_questions(path: str | Path) -> list[Question]:
    """Read the questions database, preserving row order."""
    # utf-8-sig, not utf-8: Google Sheets exports carry a byte-order mark, which
    # would otherwise become part of the first column's header name.
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    questions = []
    for number, row in enumerate(rows, start=1):
        response_type = normalize(row["Question Response Type"])
        weight = int(normalize(row["Weight"]) or 0)
        mentor_question = row["Mentor Question"].strip()
        mentor_options = _parse_options(row["Mentor Response Options"])
        mentee_options = _parse_options(row["Mentee Response Options"])
        is_natural_language = (
            normalize(row["Mentor Response Options"])
            == normalize(NATURAL_LANGUAGE_MARKER)
        )
        role = _route(response_type, weight, mentor_question, is_natural_language)

        criteria = row["Response Matching Criteria (any order)"]
        choice_scores = None
        overlap_thresholds = None
        if role == ROLE_MULTIPLE_CHOICE:
            choice_scores = _parse_choice_scores(criteria, mentor_options, mentee_options)
        elif role == ROLE_CHECKBOX:
            overlap_thresholds = _parse_overlap_thresholds(criteria)

        mentor_required, mentee_required = _parse_required(row["Question Required?"])
        mentee_question = row["Mentee Question"].strip()

        questions.append(
            Question(
                row=number,
                role=role,
                weight=weight,
                mentor_question=mentor_question,
                mentee_question=None if _is_na(mentee_question) else mentee_question,
                mentor_required=mentor_required,
                mentee_required=mentee_required,
                mentor_options=mentor_options,
                mentee_options=mentee_options,
                percentiles=_parse_percentiles(row["Similarity Percentile Cutoffs"]),
                choice_scores=choice_scores,
                overlap_thresholds=overlap_thresholds,
            )
        )
    return questions


# --- 2. the form exports --------------------------------------------------

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


# --- 3. parsing answers ---------------------------------------------------

KIND_BLANK = "blank"
KIND_CHOICE = "choice"
KIND_CHECKBOX = "checkbox"
KIND_TEXT = "text"


@dataclass(frozen=True)
class Response:
    """One respondent's answer to one question. Plain immutable record."""

    row: int
    kind: str
    # The cell exactly as exported, kept for display.
    text: str
    # Option numbers the answer resolved to, in the order they were selected.
    indices: tuple[int, ...]
    # Original text of any part of the answer that matched no listed option.
    write_ins: tuple[str, ...]


def _options_for(question: Question, side: str) -> tuple[Option, ...]:
    return question.mentor_options if side == MENTOR else question.mentee_options


def _index_lookup(options: tuple[Option, ...]) -> dict[str, int]:
    """Map normalized option text to its index.

    Options marked as write-ins are left out: their text is the literal word
    "Other", which Google Forms never exports, and which carries no meaning for
    scoring even if someone types it.
    """
    return {
        normalize(option.text): option.index
        for option in options
        if option.text and not option.is_write_in
    }


def _split_checkbox(cell: str, lookup: dict[str, int]) -> list[str]:
    """Split a checkbox cell into the individual options that were selected.

    Google Forms joins selections with commas while the database separates
    options with semicolons, so the cell has to be split on commas. An option's
    own text may contain a comma, so adjacent pieces are re-joined whenever the
    longer run is itself a listed option.
    """
    pieces = cell.split(",")
    selections = []
    start = 0
    while start < len(pieces):
        for end in range(len(pieces), start, -1):
            candidate = ",".join(pieces[start:end])
            if normalize(candidate) in lookup:
                selections.append(candidate)
                start = end
                break
        else:
            selections.append(pieces[start])
            start += 1
    return selections


def _parse_choice(row: int, cell: str, lookup: dict[str, int]) -> Response:
    index = lookup.get(normalize(cell))
    if index is None:
        return Response(
            row=row, kind=KIND_CHOICE, text=cell, indices=(), write_ins=(cell,)
        )
    return Response(
        row=row, kind=KIND_CHOICE, text=cell, indices=(index,), write_ins=()
    )


def _parse_checkbox(row: int, cell: str, lookup: dict[str, int]) -> Response:
    indices = []
    write_ins = []
    for selection in _split_checkbox(cell, lookup):
        index = lookup.get(normalize(selection))
        if index is None:
            write_ins.append(selection.strip())
        elif index not in indices:
            indices.append(index)
    return Response(
        row=row,
        kind=KIND_CHECKBOX,
        text=cell,
        indices=tuple(indices),
        write_ins=tuple(write_ins),
    )


def parse_response(question: Question, side: str, cell: str) -> Response:
    """Resolve one answer cell against its question's option list."""
    if is_blank(cell):
        return Response(
            row=question.row, kind=KIND_BLANK, text="", indices=(), write_ins=()
        )

    if question.role == ROLE_MULTIPLE_CHOICE:
        return _parse_choice(question.row, cell, _index_lookup(_options_for(question, side)))
    if question.role == ROLE_CHECKBOX:
        return _parse_checkbox(
            question.row, cell, _index_lookup(_options_for(question, side))
        )

    # Natural language, location, and avoid rows keep their text as written.
    return Response(
        row=question.row, kind=KIND_TEXT, text=cell, indices=(), write_ins=()
    )


def parse_responses(
    questions: list[Question], respondent: Respondent
) -> dict[int, Response]:
    """Parse every answer a respondent gave, keyed by question row."""
    return {
        question.row: parse_response(
            question, respondent.side, respondent.responses[question.row]
        )
        for question in questions
        if question.row in respondent.responses
    }


# --- 4. embedding and write-ins -------------------------------------------

@lru_cache(maxsize=1)
def load_model():
    """Load the sentence-transformer, reusing it for the life of the process."""
    # Imported here rather than at module level: pulling in torch costs seconds,
    # and the API server should not pay that just to serve an upload page.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL)


def collect_texts(
    questions: list[Question], answer_sets: Iterable[dict[int, Response]]
) -> list[str]:
    """Gather every distinct string that will need a vector.

    That is the answers to semantic questions, every write-in, and the listed
    options of any question a write-in appeared on, since resolving a write-in
    means comparing it against those options.
    """
    by_row = {question.row: question for question in questions}
    texts: set[str] = set()
    write_in_rows: set[int] = set()

    for answers in answer_sets:
        for row, response in answers.items():
            question = by_row.get(row)
            if question is None or response.kind == KIND_BLANK:
                continue
            if question.role == ROLE_SEMANTIC:
                texts.add(normalize(response.text))
            for write_in in response.write_ins:
                texts.add(normalize(write_in))
                write_in_rows.add(row)

    for row in write_in_rows:
        question = by_row[row]
        for option in question.mentor_options + question.mentee_options:
            if not option.is_write_in:
                texts.add(normalize(option.text))

    texts.discard("")
    # Sorted so the batch order, and therefore the run, is reproducible.
    return sorted(texts)


def embed(texts: Iterable[str]) -> dict[str, np.ndarray]:
    """Embed each string once, keyed by its normalized form."""
    ordered = list(texts)
    if not ordered:
        return {}

    vectors = load_model().encode(
        ordered,
        # Unit length, so cosine similarity is a plain dot product downstream.
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    logger.info("embedded %d unique responses", len(ordered))
    return dict(zip(ordered, vectors))


def build_cache(
    questions: list[Question], answer_sets: Iterable[dict[int, Response]]
) -> dict[str, np.ndarray]:
    """Collect and embed everything the run needs, before any pair scoring."""
    return embed(collect_texts(questions, answer_sets))


def similarity(cache: dict[str, np.ndarray], left: str, right: str) -> float:
    """Cosine similarity between two responses, from their cached vectors."""
    left_key, right_key = normalize(left), normalize(right)
    if not left_key or not right_key:
        return 0.0

    missing = [key for key in (left_key, right_key) if key not in cache]
    if missing:
        # Only reachable if collection missed a string, which is a bug rather
        # than a data problem, so it should surface loudly.
        raise KeyError(f"no cached embedding for {missing[0]!r}")

    return float(np.dot(cache[left_key], cache[right_key]))


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
