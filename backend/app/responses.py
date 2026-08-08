"""Turn a respondent's raw answer cells into structured responses.

Each cell is resolved once, here, against the option list for its question and
side. Everything downstream reads option indices rather than text, so the
differently worded options on the feedback question never matter.

Anything that matches no listed option is carried forward as a write-in with
its original text intact, for Step 8 to resolve by embedding.
"""

from dataclasses import dataclass

from app.normalize import is_blank, normalize
from app.questions import (
    ROLE_CHECKBOX,
    ROLE_MULTIPLE_CHOICE,
    Option,
    Question,
)
from app.inputs import MENTOR, Respondent

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


def is_answered(response: Response) -> bool:
    return response.kind != KIND_BLANK


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
