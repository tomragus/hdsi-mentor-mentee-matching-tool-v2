"""Tests for the avoid constraint, the assignment, and the report."""

from pathlib import Path

import pytest

from app.inputs import (
    MENTEE,
    MENTOR,
    Respondent,
    ROLE_AVOID,
    ROLE_LOCATION,
    build_respondents,
    link_columns,
    load_questions,
    read_export,
)
from app.matching import (
    Assignment,
    PairScore,
    Participant,
    Solution,
    blocked_cells,
    build_vocabulary,
    extract_avoid_terms,
    keyword_extractor,
    prepare,
    score_all,
    solve,
    stated_terms_for_all,
)
from app.main import build_report


FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parents[2]
# Cohort A, which is the oversubscribed one. The waitlist and capacity tests
# below need mentees to outnumber mentor slots, which cohort B deliberately
# does not do.
SYNTHETIC_MENTOR = ROOT / "Synthetic A - Alumni Mentor Questionnaire (Responses).csv"
SYNTHETIC_MENTEE = ROOT / "Synthetic A - Student Mentee Questionnaire (Responses).csv"
REAL_MENTOR = FIXTURES / "mentor_responses.csv"
REAL_MENTEE = FIXTURES / "mentee_responses.csv"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


@pytest.fixture(scope="module")
def questions():
    return load_questions(DATABASE)


def cohort(mentor_path: Path, mentee_path: Path, questions):
    mentor_frame = read_export(mentor_path)
    mentee_frame = read_export(mentee_path)
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees = build_respondents(questions, links, mentee_frame, MENTEE)
    return mentors + mentees


@pytest.fixture(scope="module")
def real_people(real_exports, questions):
    return cohort(REAL_MENTOR, REAL_MENTEE, questions)


@pytest.fixture(scope="module")
def synthetic_people(questions):
    return cohort(SYNTHETIC_MENTOR, SYNTHETIC_MENTEE, questions)


@pytest.fixture
def avoid_question(questions):
    return next(q for q in questions if q.role == ROLE_AVOID)


def test_vocabulary_drops_terms_too_generic_to_match_on(questions, real_people):
    """"R" and "AI" would match nearly everyone and block whole cohorts."""
    vocabulary = build_vocabulary(questions, real_people)
    assert "r" not in vocabulary
    assert "ai" not in vocabulary
    assert "data" not in vocabulary
    assert "etc" not in vocabulary


def test_keyword_extractor_matches_whole_words_only():
    vocabulary = ("finance", "computer vision")
    assert keyword_extractor("prefer to avoid finance", vocabulary) == {"finance"}
    assert keyword_extractor("I work in refinancing", vocabulary) == set()


def test_a_failed_extraction_is_not_fatal(
    questions, synthetic_people, avoid_question
):
    """A model call that fails must not stop the run; it just blocks nobody."""
    vocabulary = build_vocabulary(questions, synthetic_people)

    def always_fails(text: str, terms: tuple[str, ...]) -> None:
        return None

    extracted = extract_avoid_terms(
        avoid_question, synthetic_people, vocabulary, extractor=always_fails
    )
    assert extracted == {}


def person(key: str, side: str) -> Respondent:
    """A respondent identified only by key, since blocking works on keys."""
    return Respondent(
        key=key,
        side=side,
        name=key,
        email=key,
        capacity=1,
        submitted_at=None,
        responses={},
    )


def test_a_mentees_preference_blocks_the_pair():
    """The constraint runs both ways, not just from the mentor's side."""
    blocked = blocked_cells(
        [person("m", MENTOR)],
        [person("e", MENTEE)],
        {"e": {"insurance"}},
        {"m": {"insurance"}},
    )
    assert blocked == {("m", "e")}


def test_matching_is_exact_not_partial():
    """A closed vocabulary is what keeps this from firing on loose similarity."""
    blocked = blocked_cells(
        [person("m", MENTOR)],
        [person("e", MENTEE)],
        {"m": {"investment banking"}},
        {"e": {"banking"}},
    )
    assert blocked == set()


def participant(key: str, side: str, capacity: int = 1) -> Participant:
    return Participant(
        respondent=Respondent(
            key=key,
            side=side,
            name=key.upper(),
            email=key,
            capacity=capacity,
            submitted_at=None,
            responses={},
        ),
        answers={},
    )


def table(values: dict[tuple[str, str], float]) -> dict[tuple[str, str], PairScore]:
    """Score table built straight from normalized values."""
    return {
        pair: PairScore(
            mentor_key=pair[0],
            mentee_key=pair[1],
            raw=int(value * 100),
            maximum=100,
            normalized=value,
            question_scores=(),
        )
        for pair, value in values.items()
    }


def test_the_global_solve_beats_picking_greedily():
    """The failure mode this whole step exists to avoid.

    Greedy takes m1 x e1 at 0.90 first, which strands e2 with m2 at 0.00 for a
    total of 0.90. Solving globally gives up the best single pair to reach 1.05.
    """
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    scores = table(
        {
            ("m1", "e1"): 0.90,
            ("m1", "e2"): 0.85,
            ("m2", "e1"): 0.20,
            ("m2", "e2"): 0.00,
        }
    )

    solution = solve(mentors, mentees, scores)
    total = sum(a.score.normalized for a in solution.assignments)

    assert total == pytest.approx(1.05)
    pairs = {(a.mentor_key, a.mentee_key) for a in solution.assignments}
    assert pairs == {("m1", "e2"), ("m2", "e1")}


def test_a_blocked_pair_is_never_assigned():
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m1", "e"): 0.9, ("m2", "e"): 0.1})

    solution = solve(mentors, mentees, scores, blocked={("m1", "e")})

    assert {(a.mentor_key, a.mentee_key) for a in solution.assignments} == {("m2", "e")}


def test_a_fully_blocked_mentee_is_waitlisted_rather_than_forced():
    """Blocking must not make the problem unsolvable."""
    mentors = [participant("m", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m", "e"): 0.9})

    solution = solve(mentors, mentees, scores, blocked={("m", "e")})

    assert solution.assignments == ()
    assert solution.unassigned == ("e",)


def test_exact_ties_resolve_the_same_way_every_run():
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m1", "e"): 0.5, ("m2", "e"): 0.5})

    first = solve(mentors, mentees, scores)
    second = solve(mentors, mentees, scores)

    assert first.assignments == second.assignments


def run(mentor_path: Path, mentee_path: Path, questions):
    mentor_frame = read_export(mentor_path)
    mentee_frame = read_export(mentee_path)
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, mentees, scoring = prepare(questions, links, mentor_frame, mentee_frame)
    scores = score_all(scoring, mentors, mentees)

    people = [p.respondent for p in mentors + mentees]
    avoid_question = next(q for q in questions if q.role == ROLE_AVOID)
    vocabulary = build_vocabulary(questions, people)
    blocked = blocked_cells(
        [p.respondent for p in mentors],
        [p.respondent for p in mentees],
        extract_avoid_terms(avoid_question, people, vocabulary),
        stated_terms_for_all(questions, people, vocabulary),
    )
    return mentors, mentees, scores, blocked


@pytest.fixture(scope="module")
def synthetic_run(questions):
    return run(SYNTHETIC_MENTOR, SYNTHETIC_MENTEE, questions)


def test_blocked_pairings_are_never_assigned(synthetic_run):
    """The avoid constraint has to survive the solve, not just the matrix."""
    mentors, mentees, scores, blocked = synthetic_run
    solution = solve(mentors, mentees, scores, blocked=blocked)
    assigned = {(a.mentor_key, a.mentee_key) for a in solution.assignments}
    assert blocked, "the synthetic cohort must exercise this path"
    assert not (assigned & blocked)


def test_no_mentor_exceeds_their_stated_capacity(synthetic_run):
    mentors, mentees, scores, blocked = synthetic_run
    solution = solve(mentors, mentees, scores, blocked=blocked)

    capacity = {m.respondent.key: max(1, m.respondent.capacity) for m in mentors}
    taken: dict[str, int] = {}
    for assignment in solution.assignments:
        taken[assignment.mentor_key] = taken.get(assignment.mentor_key, 0) + 1
    assert all(count <= capacity[key] for key, count in taken.items())


def report_for(
    mentors: list[Participant], mentees: list[Participant], pairs: list[tuple[str, str]]
) -> dict:
    """Run the report over a hand-built solution."""
    scores = table({pair: 0.7 for pair in pairs})
    assigned = {mentee for _, mentee in pairs}
    return build_report(
        mentors,
        mentees,
        Solution(
            assignments=tuple(
                Assignment(mentor_key=m, mentee_key=e, score=scores[(m, e)])
                for m, e in pairs
            ),
            unassigned=tuple(
                mentee.respondent.key
                for mentee in mentees
                if mentee.respondent.key not in assigned
            ),
        ),
    )


def test_an_unfilled_slot_does_not_make_a_mentor_unmatched():
    """A mentor who offered two places and filled one still has a mentee."""
    mentors = [participant("m1", MENTOR, capacity=2), participant("m2", MENTOR)]
    report = report_for(mentors, [participant("e1", MENTEE)], [("m1", "e1")])
    assert [m["mentor_key"] for m in report["unmatched_mentors"]] == ["m2"]


def test_every_mentor_placed_counts_as_none_unmatched():
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    report = report_for(mentors, mentees, [("m1", "e1"), ("m2", "e2")])
    assert report["unmatched_mentors"] == []
    assert report["waitlist"] == []


def test_an_unassigned_mentee_lands_on_the_waitlist():
    mentors = [participant("m1", MENTOR)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    report = report_for(mentors, mentees, [("m1", "e1")])
    assert [e["mentee_key"] for e in report["waitlist"]] == ["e2"]


@pytest.fixture(scope="module")
def synthetic_cohort():
    questions = load_questions(DATABASE)
    mentor_frame = read_export(SYNTHETIC_MENTOR)
    mentee_frame = read_export(SYNTHETIC_MENTEE)
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees = build_respondents(questions, links, mentee_frame, MENTEE)
    return questions, mentors, mentees


def test_mentees_outnumber_mentor_slots(synthetic_cohort):
    """Without this the waitlist path is unreachable."""
    _, mentors, mentees = synthetic_cohort
    assert sum(mentor.capacity for mentor in mentors) < len(mentees)


def test_enough_avoid_answers_to_fire_the_constraint(synthetic_cohort):
    questions, mentors, mentees = synthetic_cohort
    avoid = next(q for q in questions if q.role == ROLE_AVOID)
    answered = [
        person
        for person in mentors + mentees
        if person.responses.get(avoid.row, "").strip()
    ]
    assert len(answered) >= 15


def test_locations_span_several_time_zones(synthetic_cohort):
    questions, mentors, mentees = synthetic_cohort
    location = next(q for q in questions if q.role == ROLE_LOCATION)
    stated = {person.responses[location.row] for person in mentors + mentees}
    assert len(stated) >= 10
    assert any("Pacific" in text for text in stated), "one states its own offset"
    assert any("travelling" in text.lower() for text in stated), "one is unresolvable"