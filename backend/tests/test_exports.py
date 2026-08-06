"""Tests for export loading and column linking."""

from pathlib import Path

import pytest

from app.exports import (
    ExportLinkError,
    find_timestamp_column,
    link_columns,
    read_export,
)
from app.questions import load_questions

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


@pytest.fixture
def questions():
    return load_questions(DATABASE)


@pytest.fixture
def mentor(real_exports):
    return read_export(FIXTURES / "mentor_responses.csv")


@pytest.fixture
def mentee(real_exports):
    return read_export(FIXTURES / "mentee_responses.csv")


def test_reads_every_respondent(mentor, mentee):
    assert len(mentor) == 6
    assert len(mentee) == 4


def test_reads_values_as_text(mentor):
    """Graduation years must stay strings, not become integers or floats."""
    assert all(isinstance(v, str) for v in mentor["Graduation Year"].dropna())


def test_finds_timestamp_column(mentor, mentee):
    assert find_timestamp_column(mentor) == "Timestamp"
    assert find_timestamp_column(mentee) == "Timestamp"


def test_links_every_question(questions, mentor, mentee):
    links = link_columns(questions, mentor, mentee)
    assert len(links) == len(questions)

    for question in questions:
        link = links[question.row]
        assert link.mentor_column is not None, question.mentor_question
        # Rows with no mentee counterpart legitimately have no mentee column.
        if question.mentee_question is None:
            assert link.mentee_column is None
        else:
            assert link.mentee_column is not None, question.mentee_question


def test_linking_ignores_column_order(questions, mentor, mentee):
    """Reordering a form's questions must not change the mapping."""
    expected = link_columns(questions, mentor, mentee)
    shuffled = link_columns(questions, mentor[mentor.columns[::-1]], mentee)
    assert shuffled == expected


def test_missing_question_aborts_naming_it(questions, mentor, mentee):
    renamed = mentee.rename(columns={"UCSD Email Address": "Your Email"})
    with pytest.raises(ExportLinkError) as caught:
        link_columns(questions, mentor, renamed)

    assert "UCSD Email Address" in str(caught.value)
    assert "mentee" in str(caught.value)


def test_reports_every_missing_question_at_once(questions, mentor, mentee):
    renamed = mentee.rename(
        columns={
            "UCSD Email Address": "Your Email",
            "How would you describe your communication style?": "Comms",
        }
    )
    with pytest.raises(ExportLinkError) as caught:
        link_columns(questions, mentor, renamed)

    assert len(caught.value.missing) == 2
