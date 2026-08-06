"""Tests for the location scorer."""

from pathlib import Path

import pytest

from app.exports import link_columns, read_export
from app.location import LocationOffset, resolve_offset, resolve_offsets, score_location
from app.questions import ROLE_LOCATION, load_questions
from app.respondents import MENTEE, MENTOR, build_respondents

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


@pytest.fixture
def questions():
    return load_questions(DATABASE)


@pytest.fixture
def location_question(questions):
    return next(q for q in questions if q.role == ROLE_LOCATION)


def hours(text: str) -> float | None:
    offset = resolve_offset(text)
    return None if offset is None else offset.hours


def test_reads_a_stated_difference_in_parentheses():
    """The mentor form asks people outside Pacific Time to state their offset."""
    assert hours("New Orleans, LA, USA (+2)") == 2
    assert hours("Brooklyn, NY, USA - EST time zone (+3 from san Diego)") == 3


def test_reads_a_spelled_out_difference():
    assert hours("Remote, 3 hours ahead of Pacific Time") == 3
    assert hours("Boulder, Colorado (1 hour ahead of Pacific)") == 1
    assert hours("2 hours behind Pacific") == -2


def test_a_stated_difference_wins_over_the_table():
    """Someone reporting their own offset knows better than a city lookup."""
    offset = resolve_offset("San Diego, CA (+3)")
    assert offset == LocationOffset(hours=3.0, source="stated")


def test_a_zip_code_is_not_read_as_an_offset():
    assert hours("San Diego, CA 92122-1234") == 0


def test_two_letter_codes_resolve_only_as_whole_segments():
    """"LA" here is Louisiana, and "OR" and "IN" are ordinary English words."""
    assert hours("New Orleans, LA, USA") == 2
    assert hours("Portland, OR") == 0
    assert hours("Indianapolis, IN, USA") == 3
    assert hours("Nowhere in particular or thereabouts") is None


def test_washington_the_state_and_the_district_are_told_apart():
    assert hours("Seattle, Washington, USA") == 0
    assert hours("Washington, DC, USA") == 3
    assert hours("Washington, D.C.") == 3


def test_resolves_a_place_name_inside_a_sentence():
    assert hours("I live just outside Bangalore") == 13.5


def test_unrecognizable_answers_resolve_to_nothing():
    """Guessing would silently distort a score, so these stay unresolved."""
    assert resolve_offset("Why does this matter? ") is None
    assert resolve_offset("Currently travelling") is None
    assert resolve_offset("") is None


def test_scoring_bands(questions):
    offsets = {
        "same": LocationOffset(0, "lookup"),
        "one": LocationOffset(1, "lookup"),
        "two": LocationOffset(2, "lookup"),
        "three": LocationOffset(3, "lookup"),
        "far": LocationOffset(13.5, "lookup"),
    }
    assert score_location("same", "same", offsets) == 10
    assert score_location("same", "one", offsets) == 5
    assert score_location("same", "two", offsets) == 5
    assert score_location("same", "three", offsets) == 0
    assert score_location("same", "far", offsets) == 0


def test_scoring_is_symmetric():
    offsets = {"a": LocationOffset(3, "lookup"), "b": LocationOffset(1, "lookup")}
    assert score_location("a", "b", offsets) == score_location("b", "a", offsets)


def test_an_unresolved_side_makes_the_pair_unscorable():
    """Like a blank answer, it drops the question rather than scoring it zero."""
    offsets = {"a": LocationOffset(0, "lookup")}
    assert score_location("a", "missing", offsets) is None
    assert score_location("missing", "a", offsets) is None


def test_real_cohort_resolves_all_but_the_one_non_answer(real_exports, questions, location_question):
    mentor_frame = read_export(FIXTURES / "mentor_responses.csv")
    mentee_frame = read_export(FIXTURES / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, _ = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, _ = build_respondents(questions, links, mentee_frame, MENTEE)

    offsets, flags = resolve_offsets(location_question, mentors + mentees)

    assert len(offsets) == 9
    assert len(flags) == 1, "only 'Why does this matter?' cannot be read"
    assert "matter" in flags[0].reason


def test_synthetic_cohort_reaches_every_scoring_band(questions, location_question):
    """The real sample is all one time zone, so the bands are checked here."""
    mentor_frame = read_export(SYNTHETIC / "mentor_responses.csv")
    mentee_frame = read_export(SYNTHETIC / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, _ = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, _ = build_respondents(questions, links, mentee_frame, MENTEE)

    offsets, flags = resolve_offsets(location_question, mentors + mentees)
    points = {
        score_location(mentor.key, mentee.key, offsets)
        for mentor in mentors
        for mentee in mentees
    }

    assert points == {0, 5, 10, None}, "every band, plus unresolved pairs"
    assert flags, "the two deliberately unreadable locations are flagged"
    assert {offset.source for offset in offsets.values()} == {"stated", "lookup"}
