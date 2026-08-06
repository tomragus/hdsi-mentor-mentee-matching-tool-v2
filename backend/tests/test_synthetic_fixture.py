"""Guards on the synthetic fixture.

Later steps rely on this cohort reaching situations the real sample never does.
If a regeneration stops producing one of them, the step that depends on it
would still pass its own tests while testing nothing, so the properties are
asserted here instead.
"""

from pathlib import Path

import pytest

from app.exports import link_columns, read_export
from app.questions import ROLE_AVOID, ROLE_LOCATION, load_questions
from app.respondents import MENTEE, MENTOR, build_respondents
from app.responses import parse_responses

SYNTHETIC = Path(__file__).parent / "fixtures" / "synthetic"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


@pytest.fixture(scope="module")
def cohort():
    questions = load_questions(DATABASE)
    mentor_frame = read_export(SYNTHETIC / "mentor_responses.csv")
    mentee_frame = read_export(SYNTHETIC / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, mentor_flags = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, mentee_flags = build_respondents(questions, links, mentee_frame, MENTEE)
    return questions, mentors, mentees, mentor_flags + mentee_flags


def test_links_against_the_questions_database(cohort):
    """Headers come from the database, so linking must never fail."""
    questions, mentors, mentees, _ = cohort
    assert len(mentors) == 15
    assert len(mentees) == 40


def test_resubmissions_are_collapsed(cohort):
    """Each export carries one extra row that must not become an extra person."""
    _, mentors, mentees, _ = cohort
    assert len({m.key for m in mentors}) == len(mentors)
    assert len({m.key for m in mentees}) == len(mentees)


def test_mentees_outnumber_mentor_slots(cohort):
    """Without this the waitlist and blocking-pair paths are unreachable."""
    _, mentors, mentees, _ = cohort
    assert sum(mentor.capacity for mentor in mentors) < len(mentees)


def test_both_sides_have_a_respondent_needing_review(cohort):
    _, _, _, flags = cohort
    assert {flag.side for flag in flags} == {MENTOR, MENTEE}


def test_a_blank_name_falls_back_to_the_email(cohort):
    _, mentors, mentees, _ = cohort
    assert any(person.name == person.email for person in mentors + mentees)


def test_enough_avoid_answers_to_fire_the_constraint(cohort):
    questions, mentors, mentees, _ = cohort
    avoid = next(q for q in questions if q.role == ROLE_AVOID)
    answered = [
        person
        for person in mentors + mentees
        if person.responses.get(avoid.row, "").strip()
    ]
    assert len(answered) >= 15


def test_locations_span_several_time_zones(cohort):
    questions, mentors, mentees, _ = cohort
    location = next(q for q in questions if q.role == ROLE_LOCATION)
    stated = {person.responses[location.row] for person in mentors + mentees}
    assert len(stated) >= 10
    assert any("Pacific" in text for text in stated), "one states its own offset"
    assert any("travelling" in text.lower() for text in stated), "one is unresolvable"


def test_write_ins_appear_on_both_sides(cohort):
    questions, mentors, mentees, _ = cohort
    for people, side in ((mentors, MENTOR), (mentees, MENTEE)):
        found = any(
            response.write_ins
            for person in people
            for response in parse_responses(questions, person).values()
        )
        assert found, f"{side} export contains no write-in"
