"""Tests for the multiple choice and checkbox scorers."""

from pathlib import Path

import pytest

from app.exports import link_columns, read_export
from app.questions import ROLE_CHECKBOX, ROLE_MULTIPLE_CHOICE, load_questions
from app.respondents import MENTEE, MENTOR, build_respondents
from app.responses import KIND_BLANK, KIND_CHECKBOX, KIND_CHOICE, Response, parse_responses
from app.scorers import score_checkbox, score_multiple_choice, score_options

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"

COMMITMENT_ROW = 1
COMMUNICATION_ROW = 2
FEEDBACK_ROW = 9
STYLE_ROW = 11


@pytest.fixture
def questions():
    return load_questions(DATABASE)


@pytest.fixture
def by_row(questions):
    return {question.row: question for question in questions}


def choice(row: int, index: int) -> Response:
    return Response(row=row, kind=KIND_CHOICE, text="x", indices=(index,), write_ins=())


def checkbox(row: int, *indices: int) -> Response:
    return Response(
        row=row, kind=KIND_CHECKBOX, text="x", indices=tuple(indices), write_ins=()
    )


def blank(row: int) -> Response:
    return Response(row=row, kind=KIND_BLANK, text="", indices=(), write_ins=())


def test_matching_choices_score_perfect(by_row):
    question = by_row[COMMITMENT_ROW]
    assert score_multiple_choice(question, choice(1, 1), choice(1, 1)) == 10


def test_opposed_choices_score_nothing(by_row):
    """"Yes" against "No" is the row's stated 0-point combination."""
    question = by_row[COMMITMENT_ROW]
    assert score_multiple_choice(question, choice(1, 1), choice(1, 2)) == 0


def test_partial_choices_score_good(by_row):
    question = by_row[COMMITMENT_ROW]
    assert score_multiple_choice(question, choice(1, 1), choice(1, 3)) == 5


def test_combinations_count_in_either_order(by_row):
    question = by_row[COMMITMENT_ROW]
    for mentor in (1, 2, 3):
        for mentee in (1, 2, 3):
            forward = score_multiple_choice(question, choice(1, mentor), choice(1, mentee))
            backward = score_multiple_choice(question, choice(1, mentee), choice(1, mentor))
            assert forward == backward


def test_differently_worded_options_score_by_index(by_row):
    """Row 11 words option 2 the same on both forms now, row 9 does not.

    Both must still score off the index, so a mentor choosing option 2 and a
    mentee choosing option 2 is a perfect match on either row.
    """
    for row in (FEEDBACK_ROW, STYLE_ROW):
        assert score_multiple_choice(by_row[row], choice(row, 2), choice(row, 2)) == 10


def test_checkbox_scores_on_overlap_count(by_row):
    question = by_row[COMMUNICATION_ROW]
    assert score_checkbox(question, checkbox(2, 1, 2, 3), checkbox(2, 1, 2, 3)) == 10
    assert score_checkbox(question, checkbox(2, 1, 2), checkbox(2, 2, 5)) == 5
    assert score_checkbox(question, checkbox(2, 1, 2), checkbox(2, 4, 5)) == 0


def test_checkbox_ignores_unmatched_selections(by_row):
    """Only shared options count, however many either side picked."""
    question = by_row[COMMUNICATION_ROW]
    many = checkbox(2, 1, 2, 3, 4, 5, 6)
    assert score_checkbox(question, many, checkbox(2, 3)) == 5


def test_blank_on_either_side_is_unscorable(by_row):
    """The optional-question rule needs "no score" told apart from "zero"."""
    question = by_row[COMMITMENT_ROW]
    assert score_multiple_choice(question, blank(1), choice(1, 1)) is None
    assert score_multiple_choice(question, choice(1, 1), blank(1)) is None
    assert score_checkbox(by_row[COMMUNICATION_ROW], blank(2), checkbox(2, 1)) is None


def test_dispatch_routes_by_role(by_row):
    assert by_row[COMMITMENT_ROW].role == ROLE_MULTIPLE_CHOICE
    assert by_row[COMMUNICATION_ROW].role == ROLE_CHECKBOX
    assert score_options(by_row[COMMITMENT_ROW], choice(1, 1), choice(1, 1)) == 10
    assert score_options(by_row[COMMUNICATION_ROW], checkbox(2, 1), checkbox(2, 1)) == 5


def test_dispatch_declines_other_roles(questions, by_row):
    semantic = next(q for q in questions if q.role not in (ROLE_MULTIPLE_CHOICE, ROLE_CHECKBOX))
    assert score_options(semantic, choice(semantic.row, 1), choice(semantic.row, 1)) is None


def test_every_real_pair_scores_on_option_rows(real_exports, questions):
    """No option row in the sample cohort produces a missing-criteria warning."""
    mentor_frame = read_export(FIXTURES / "mentor_responses.csv")
    mentee_frame = read_export(FIXTURES / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, _ = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, _ = build_respondents(questions, links, mentee_frame, MENTEE)

    mentor_answers = [parse_responses(questions, person) for person in mentors]
    mentee_answers = [parse_responses(questions, person) for person in mentees]
    option_rows = [
        q for q in questions if q.role in (ROLE_MULTIPLE_CHOICE, ROLE_CHECKBOX)
    ]

    scored = 0
    for mentor in mentor_answers:
        for mentee in mentee_answers:
            for question in option_rows:
                points = score_options(question, mentor[question.row], mentee[question.row])
                if points is not None:
                    assert points in (0, 5, 10)
                    scored += 1
    assert scored > 0
