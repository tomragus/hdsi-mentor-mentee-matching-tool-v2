"""Tests for write-in resolution and the write-in penalty."""

from pathlib import Path

import numpy as np
import pytest

from app.config import WRITE_IN_PENALTY
from app.embeddings import build_cache
from app.exports import link_columns, read_export
from app.normalize import normalize
from app.questions import (
    ROLE_CHECKBOX,
    ROLE_MULTIPLE_CHOICE,
    ROLE_SEMANTIC,
    Option,
    Question,
    load_questions,
)
from app.respondents import MENTEE, MENTOR, build_respondents
from app.responses import (
    KIND_CHECKBOX,
    KIND_CHOICE,
    KIND_TEXT,
    Response,
    parse_responses,
)
from app.writeins import nearest_option, penalty, resolve_response, resolve_write_ins

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"

TOPICS_ROW = 3


def question_with(options: list[tuple[str, bool]], role: str = ROLE_CHECKBOX) -> Question:
    """A stand-in question whose options can be given known vectors."""
    listed = tuple(
        Option(index=position, text=text, is_write_in=is_write_in)
        for position, (text, is_write_in) in enumerate(options, start=1)
    )
    return Question(
        row=99,
        response_type="check box",
        role=role,
        weight=1,
        mentor_question="stand-in",
        mentee_question="stand-in",
        mentor_required=False,
        mentee_required=False,
        mentor_options=listed,
        mentee_options=listed,
        is_natural_language=False,
        percentiles=(85, 50),
        choice_scores=None,
        overlap_thresholds=(),
    )


def cache_from(vectors: dict[str, list[float]]) -> dict[str, np.ndarray]:
    """Build a cache of unit vectors by hand, so similarities are exact."""
    return {
        normalize(text): np.array(values, dtype=float) / np.linalg.norm(values)
        for text, values in vectors.items()
    }


def test_nearest_option_picks_the_closest():
    question = question_with([("Resume Review", False), ("How to Network", False)])
    cache = cache_from(
        {
            "Resume Review": [1.0, 0.0],
            "How to Network": [0.0, 1.0],
            "help with my CV": [0.9, 0.1],
        }
    )
    assert nearest_option(question, MENTOR, "help with my CV", cache) == 1


def test_nearest_option_ignores_the_write_in_option():
    """"Other" is the literal word, so it says nothing about what was typed."""
    question = question_with([("Resume Review", False), ("Other", True)])
    cache = cache_from(
        {
            "Resume Review": [1.0, 0.0],
            "Other": [0.0, 1.0],
            "something else entirely": [0.0, 1.0],
        }
    )
    assert nearest_option(question, MENTOR, "something else entirely", cache) == 1


def test_ties_keep_the_lower_index():
    question = question_with([("Resume Review", False), ("How to Network", False)])
    cache = cache_from(
        {
            "Resume Review": [1.0, 0.0],
            "How to Network": [1.0, 0.0],
            "career help": [1.0, 0.0],
        }
    )
    assert nearest_option(question, MENTOR, "career help", cache) == 1


def test_resolution_adds_the_index_and_keeps_the_text():
    question = question_with([("Resume Review", False), ("How to Network", False)])
    cache = cache_from(
        {
            "Resume Review": [1.0, 0.0],
            "How to Network": [0.0, 1.0],
            "meeting people": [0.1, 0.9],
        }
    )
    response = Response(
        row=99,
        kind=KIND_CHECKBOX,
        text="Resume Review, meeting people",
        indices=(1,),
        write_ins=("meeting people",),
    )

    resolved = resolve_response(question, MENTOR, response, cache)

    assert resolved.indices == (1, 2)
    assert resolved.write_ins == ("meeting people",), "the penalty still applies"
    assert resolved.text == response.text


def test_index_already_selected_is_not_added_twice():
    question = question_with([("Resume Review", False), ("How to Network", False)])
    cache = cache_from(
        {
            "Resume Review": [1.0, 0.0],
            "How to Network": [0.0, 1.0],
            "fix my resume": [1.0, 0.0],
        }
    )
    response = Response(
        row=99,
        kind=KIND_CHECKBOX,
        text="Resume Review, fix my resume",
        indices=(1,),
        write_ins=("fix my resume",),
    )
    assert resolve_response(question, MENTOR, response, cache).indices == (1,)


def test_answers_without_write_ins_are_unchanged():
    question = question_with([("Resume Review", False)], role=ROLE_MULTIPLE_CHOICE)
    response = Response(
        row=99, kind=KIND_CHOICE, text="Resume Review", indices=(1,), write_ins=()
    )
    assert resolve_response(question, MENTOR, response, {}) is response


def test_semantic_answers_are_untouched():
    """Free-text rows have no options to resolve against."""
    question = question_with([], role=ROLE_SEMANTIC)
    response = Response(
        row=99, kind=KIND_TEXT, text="I work in biotech", indices=(), write_ins=()
    )
    assert resolve_response(question, MENTOR, response, {}) is response


def test_penalty_applies_once_regardless_of_side():
    wrote_in = Response(row=1, kind=KIND_CHOICE, text="x", indices=(1,), write_ins=("x",))
    listed = Response(row=1, kind=KIND_CHOICE, text="Yes", indices=(1,), write_ins=())

    assert penalty(listed, listed) == 0
    assert penalty(wrote_in, listed) == WRITE_IN_PENALTY
    assert penalty(listed, wrote_in) == WRITE_IN_PENALTY
    assert penalty(wrote_in, wrote_in) == WRITE_IN_PENALTY


@pytest.fixture(scope="module")
def real_run(real_exports):
    """The sample cohort, parsed and embedded, for an end-to-end check."""
    questions = load_questions(DATABASE)
    mentor_frame = read_export(FIXTURES / "mentor_responses.csv")
    mentee_frame = read_export(FIXTURES / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)

    groups = []
    for frame, side in ((mentor_frame, MENTOR), (mentee_frame, MENTEE)):
        respondents, _ = build_respondents(questions, links, frame, side)
        groups.append(
            (side, [parse_responses(questions, person) for person in respondents])
        )
    cache = build_cache(questions, [a for _, sets in groups for a in sets])
    return questions, groups, cache


def test_every_real_write_in_resolves_to_a_listed_option(real_run):
    questions, groups, cache = real_run
    by_row = {question.row: question for question in questions}
    resolved_count = 0

    for side, answer_sets in groups:
        for answers in answer_sets:
            for row, response in resolve_write_ins(
                questions, side, answers, cache
            ).items():
                if not response.write_ins:
                    continue
                resolved_count += 1
                listed = {
                    option.index
                    for option in (
                        by_row[row].mentor_options
                        if side == MENTOR
                        else by_row[row].mentee_options
                    )
                    if not option.is_write_in
                }
                assert set(response.indices) <= listed
                assert response.indices, "a write-in always lands on some option"

    assert resolved_count == 6, "the sample cohort contains six write-ins"
