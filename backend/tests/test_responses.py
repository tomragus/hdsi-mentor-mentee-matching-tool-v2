"""Tests for response parsing by question type."""

from pathlib import Path

import pytest

from app.exports import link_columns, read_export
from app.questions import (
    ROLE_CHECKBOX,
    Option,
    Question,
    load_questions,
)
from app.respondents import MENTEE, MENTOR, build_respondents
from app.responses import (
    KIND_BLANK,
    KIND_CHECKBOX,
    KIND_CHOICE,
    KIND_TEXT,
    is_answered,
    parse_response,
    parse_responses,
)

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"

COMMITMENT_ROW = 1
COMMUNICATION_ROW = 2
TOPICS_ROW = 3
FEEDBACK_ROW = 9
STYLE_ROW = 11


@pytest.fixture
def questions():
    return load_questions(DATABASE)


@pytest.fixture
def parsed(real_exports, questions):
    mentor_frame = read_export(FIXTURES / "mentor_responses.csv")
    mentee_frame = read_export(FIXTURES / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)

    people = {}
    for frame, side in ((mentor_frame, MENTOR), (mentee_frame, MENTEE)):
        respondents, _ = build_respondents(questions, links, frame, side)
        for respondent in respondents:
            # Keyed by display name, which the sample records as initials.
            people[respondent.name] = parse_responses(questions, respondent)
    return people


def checkbox_question(options: list[str]) -> Question:
    """A stand-in checkbox row, for cases the real database has no example of."""
    listed = tuple(
        Option(index=position, text=text, is_write_in=False)
        for position, text in enumerate(options, start=1)
    )
    return Question(
        row=99,
        response_type="check box",
        role=ROLE_CHECKBOX,
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


def test_multiple_choice_recovers_the_selected_index(questions, parsed):
    response = parsed["AG"][COMMITMENT_ROW]
    assert response.kind == KIND_CHOICE
    assert response.indices == (1,)
    assert response.write_ins == ()


def test_checkbox_recovers_every_selected_index(questions, parsed):
    response = parsed["AK"][COMMUNICATION_ROW]
    assert response.kind == KIND_CHECKBOX
    assert response.indices == (1, 2, 3, 4, 5, 6)


def test_blank_cell_is_no_response(questions, parsed):
    """A skipped optional question must be distinguishable from an answered one."""
    blanks = [
        response
        for answers in parsed.values()
        for response in answers.values()
        if response.kind == KIND_BLANK
    ]
    assert blanks, "the sample exports contain skipped questions"
    assert all(not is_answered(response) for response in blanks)
    assert all(response.indices == () for response in blanks)


def test_natural_language_response_is_retained(questions, parsed):
    industry = next(
        q.row for q in questions if q.mentor_question.startswith("In a word or two")
    )
    response = parsed["AG"][industry]
    assert response.kind == KIND_TEXT
    assert response.text.strip() != ""
    assert response.indices == ()


def test_unlisted_multiple_choice_answer_becomes_a_write_in(questions, parsed):
    """One mentor answered the feedback question in their own words."""
    response = next(
        answers[FEEDBACK_ROW]
        for answers in parsed.values()
        if answers[FEEDBACK_ROW].write_ins
    )
    assert response.indices == ()
    assert response.write_ins == (response.text,), "original text is carried forward"


def test_checkbox_write_in_keeps_the_listed_selections(questions, parsed):
    """One free-text entry must not discard the options selected alongside it."""
    response = parsed["AK"][TOPICS_ROW]
    assert response.indices == (2, 3, 4, 5, 6)
    assert response.write_ins == ("whatever the mentee needs",)


def test_differently_worded_options_align_by_index(questions, parsed):
    """Rows 9 and 11 word their options differently on each side.

    Each side is matched against its own option list, so the same choice
    resolves to the same index regardless of wording.
    """
    mentor = parsed["PS"]
    mentee = parsed["KJ"]
    assert mentor[STYLE_ROW].indices == (2,)
    assert mentee[STYLE_ROW].indices == (2,)
    assert mentor[FEEDBACK_ROW].indices == (3,)
    assert mentee[FEEDBACK_ROW].indices == (3,)


def test_no_real_response_is_wrongly_called_a_write_in(questions, parsed):
    """Every write-in in the sample exports is genuine free text.

    A listed option misread as a write-in would cost that pair a 5-point
    penalty it did not earn, which is what this guards.
    """
    write_ins = {
        text
        for answers in parsed.values()
        for response in answers.values()
        for text in response.write_ins
    }
    assert write_ins == {
        "whatever the mentee needs",
        "Anything but email.",
        "Applications of data science outside of the traditional sense",
        "Communication is one of the most important skills I will adapt to "
        "whatever will resonate with you",
        "Effective Communication Skills",
        "Technical skills",
    }


def test_checkbox_option_containing_a_comma_is_not_split():
    question = checkbox_question(["In Person", "Both, depending on the day", "Email"])
    response = parse_response(question, MENTOR, "In Person, Both, depending on the day")
    assert response.indices == (1, 2)
    assert response.write_ins == ()


def test_literal_other_does_not_resolve_to_the_write_in_option(questions):
    """"Other" carries no scoring meaning, so it stays unresolved."""
    topics = next(q for q in questions if q.row == TOPICS_ROW)
    response = parse_response(topics, MENTOR, "Resume Review, Other")
    assert response.indices == (1,)
    assert response.write_ins == ("Other",)
