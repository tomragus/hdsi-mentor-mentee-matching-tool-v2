"""Loader for the mentor/mentee questions database.

One record per CSV row, in file order. Everything downstream keys off these
records: the mentor and mentee questions are paired by row, and their response
options align by index.
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import (
    AVOID_QUESTION_PREFIX,
    DEFAULT_PERCENTILES,
    DISPLAY_ORDER,
    LOCATION_QUESTION_PREFIX,
)
from app.normalize import is_blank, normalize

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
