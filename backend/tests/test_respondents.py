"""Tests for deduplication and respondent records."""

from pathlib import Path

import pandas as pd
import pytest

from app.exports import link_columns, read_export
from app.questions import load_questions
from app.respondents import MENTEE, MENTOR, build_respondents

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"

MENTOR_EMAIL = "Email Address"
MENTEE_EMAIL = "UCSD Email Address"
CAPACITY = "How many mentees would you like to be matched with?"


@pytest.fixture
def questions():
    return load_questions(DATABASE)


@pytest.fixture
def frames(real_exports):
    return (
        read_export(FIXTURES / "mentor_responses.csv"),
        read_export(FIXTURES / "mentee_responses.csv"),
    )


@pytest.fixture
def links(questions, frames):
    return link_columns(questions, *frames)


def build(questions, links, frame, side):
    return build_respondents(questions, links, frame, side)


def test_builds_one_record_per_respondent(questions, links, frames):
    mentors, _ = build(questions, links, frames[0], MENTOR)
    mentees, _ = build(questions, links, frames[1], MENTEE)
    assert len(mentors) == 6
    assert len(mentees) == 4


def test_email_key_ignores_surrounding_text(questions, links, frames):
    """"[not required] someone@ucsd.edu" must key on the address alone."""
    raw = frames[1][MENTEE_EMAIL].dropna()
    assert any("not required" in cell for cell in raw), "the sample has such a cell"

    mentees, _ = build(questions, links, frames[1], MENTEE)
    addresses = [m.key for m in mentees if "@" in m.key]
    assert addresses
    assert all(" " not in key and "[" not in key for key in addresses)


def test_missing_email_is_kept_and_flagged(questions, links, frames):
    mentees, flags = build(questions, links, frames[1], MENTEE)
    assert len(mentees) == 4, "a respondent without an email is still matched"
    assert len(flags) == 1
    assert flags[0].side == MENTEE
    assert "email" in flags[0].reason


def test_blank_name_falls_back_to_email(questions, links, frames):
    """One mentor left the name field blank, so their address identifies them."""
    mentors, _ = build(questions, links, frames[0], MENTOR)
    nameless = [m for m in mentors if m.name == m.email]
    assert len(nameless) == 1
    assert nameless[0].email


def test_reads_mentor_capacity(questions, links, frames):
    mentors, _ = build(questions, links, frames[0], MENTOR)
    # Keyed by display name, which the sample records as initials.
    capacities = {m.name: m.capacity for m in mentors}
    assert capacities["AG"] == 2
    assert capacities["AK"] == 1


def test_mentees_have_no_capacity_question(questions, links, frames):
    mentees, _ = build(questions, links, frames[1], MENTEE)
    assert all(m.capacity == 1 for m in mentees)


def test_keeps_original_response_text(questions, links, frames):
    """Responses are shown to a coordinator, so they must not be normalized."""
    mentors, _ = build(questions, links, frames[0], MENTOR)
    industry_row = next(
        q.row for q in questions if q.mentor_question.startswith("In a word or two")
    )
    answers = [m.responses[industry_row] for m in mentors]
    assert any(any(c.isupper() for c in a) for a in answers)


def test_duplicate_submissions_keep_the_latest(questions, links, frames):
    mentor = frames[0]
    resubmission = mentor.iloc[[0]].copy()
    resubmission["Timestamp"] = "12/31/2026 23:59:59"
    resubmission[CAPACITY] = "One"
    combined = pd.concat([mentor, resubmission], ignore_index=True)

    mentors, _ = build(questions, links, combined, MENTOR)

    assert len(mentors) == 6, "the resubmission replaces rather than adds"
    kept = next(m for m in mentors if m.name == "AG")
    assert kept.capacity == 1, "the later answer wins"


def test_duplicate_detection_ignores_email_case(questions, links, frames):
    mentor = frames[0]
    resubmission = mentor.iloc[[0]].copy()
    # Same address, shouted and padded.
    resubmission[MENTOR_EMAIL] = f"  {mentor.iloc[0][MENTOR_EMAIL].upper()}  "
    resubmission["Timestamp"] = "12/31/2026 23:59:59"
    combined = pd.concat([mentor, resubmission], ignore_index=True)

    mentors, _ = build(questions, links, combined, MENTOR)
    assert len(mentors) == 6


def test_earlier_resubmission_does_not_replace_later(questions, links, frames):
    mentor = frames[0]
    stale = mentor.iloc[[0]].copy()
    stale["Timestamp"] = "01/01/2020 00:00:00"
    stale[CAPACITY] = "One"
    combined = pd.concat([mentor, stale], ignore_index=True)

    mentors, _ = build(questions, links, combined, MENTOR)
    kept = next(m for m in mentors if m.name == "AG")
    assert kept.capacity == 2, "the original, newer submission is retained"
