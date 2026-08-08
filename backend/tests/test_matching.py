"""Tests for the avoid constraint, the assignment, and the report."""

from pathlib import Path

import pytest

from app.avoid import (
    blocked_cells,
    blocked_pairs,
    build_vocabulary,
    extract_avoid_terms,
    keyword_extractor,
    stated_terms_for_all,
)
from app.inputs import (
    MENTEE,
    MENTOR,
    Respondent,
    build_respondents,
    link_columns,
    read_export,
)
from app.matching import PairScore, Participant, prepare, score_all, solve
from app.questions import ROLE_AVOID, ROLE_LOCATION, load_questions
from app.report import build_report, build_waitlist, find_blocking_pairs


FIXTURES = Path(__file__).parent / "fixtures"


ROOT = Path(__file__).parents[2]


SYNTHETIC_MENTOR = ROOT / "Synthetic Alumni Mentor Questionnaire (Responses).csv"


SYNTHETIC_MENTEE = ROOT / "Synthetic Student Mentee Questionnaire (Responses).csv"


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
    mentors, _ = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, _ = build_respondents(questions, links, mentee_frame, MENTEE)
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


def test_a_failed_extraction_is_flagged_rather_than_fatal(
    questions, synthetic_people, avoid_question
):
    """A model call that fails must not stop the run."""
    vocabulary = build_vocabulary(questions, synthetic_people)

    def always_fails(text: str, terms: tuple[str, ...]) -> None:
        return None

    extracted, flags = extract_avoid_terms(
        avoid_question, synthetic_people, vocabulary, extractor=always_fails
    )
    assert extracted == {}
    assert flags, "every unresolvable answer is raised for review"
    assert all("could not extract" in flag.reason for flag in flags)


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
    mentor = person("m", MENTOR)
    mentee = person("e", MENTEE)
    blocks = blocked_pairs(
        [mentor], [mentee], {"e": {"insurance"}}, {"m": {"insurance"}}
    )
    assert len(blocks) == 1
    assert blocks[0].mentor_triggers == ()
    assert blocks[0].mentee_triggers == ("insurance",)


def test_matching_is_exact_not_partial():
    """A closed vocabulary is what keeps this from firing on loose similarity."""
    blocks = blocked_pairs(
        [person("m", MENTOR)],
        [person("e", MENTEE)],
        {"m": {"investment banking"}},
        {"e": {"banking"}},
    )
    assert blocks == []


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
    mentors, mentees, scoring, _ = prepare(questions, links, mentor_frame, mentee_frame)
    scores = score_all(scoring, mentors, mentees)

    people = [p.respondent for p in mentors + mentees]
    avoid_question = next(q for q in questions if q.role == ROLE_AVOID)
    vocabulary = build_vocabulary(questions, people)
    extracted, _ = extract_avoid_terms(avoid_question, people, vocabulary)
    stated = stated_terms_for_all(questions, people, vocabulary)
    blocks = blocked_pairs(
        [p.respondent for p in mentors], [p.respondent for p in mentees], extracted, stated
    )
    return mentors, mentees, scores, blocked_cells(blocks)


@pytest.fixture(scope="module")
def synthetic_run(questions):
    return run(SYNTHETIC_MENTOR, SYNTHETIC_MENTEE, questions)


def test_no_mentor_exceeds_their_stated_capacity(synthetic_run):
    mentors, mentees, scores, blocked = synthetic_run
    solution = solve(mentors, mentees, scores, blocked=blocked)

    capacity = {m.respondent.key: max(1, m.respondent.capacity) for m in mentors}
    taken: dict[str, int] = {}
    for assignment in solution.assignments:
        taken[assignment.mentor_key] = taken.get(assignment.mentor_key, 0) + 1
    assert all(count <= capacity[key] for key, count in taken.items())


CONTESTED = table(
    {
        ("m1", "e1"): 0.90,
        ("m1", "e2"): 0.85,
        ("m2", "e1"): 0.20,
        ("m2", "e2"): 0.00,
    }
)


def test_a_global_optimum_can_contain_a_blocking_pair():
    """The reason this detection exists at all.

    The solve gives m1 to e2 because that is best for the cohort. But m1 would
    rather have e1, and e1 would rather have m1, so the pair is reported.
    """
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    solution = solve(mentors, mentees, CONTESTED)

    found = find_blocking_pairs(mentors, mentees, CONTESTED, solution.assignments, set())

    assert len(found) == 1
    assert (found[0].mentor_key, found[0].mentee_key) == ("m1", "e1")
    assert found[0].percentage == pytest.approx(90)
    assert found[0].mentor_current_percentage == pytest.approx(85)
    assert found[0].mentee_current_percentage == pytest.approx(20)


def test_an_excluded_pair_is_never_reported_as_blocking():
    """A pair nobody is allowed to make is not an instability to fix."""
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    excluded = {("m1", "e1")}
    solution = solve(mentors, mentees, CONTESTED, blocked=excluded)

    found = find_blocking_pairs(
        mentors, mentees, CONTESTED, solution.assignments, excluded
    )
    assert found == []


def test_waitlist_is_ordered_by_best_available_score():
    mentees = [participant(f"e{i}", MENTEE) for i in range(3)]
    scores = table({("m", "e0"): 0.4, ("m", "e1"): 0.9, ("m", "e2"): 0.6})

    entries = build_waitlist(mentees, ("e0", "e1", "e2"), scores, set())

    assert [entry.mentee_key for entry in entries] == ["e1", "e2", "e0"]
    assert entries[0].best_percentage == pytest.approx(90)
    assert entries[0].best_mentor_key == "m"


@pytest.fixture(scope="module")
def synthetic_cohort():
    questions = load_questions(DATABASE)
    mentor_frame = read_export(SYNTHETIC_MENTOR)
    mentee_frame = read_export(SYNTHETIC_MENTEE)
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, mentor_flags = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, mentee_flags = build_respondents(questions, links, mentee_frame, MENTEE)
    return questions, mentors, mentees, mentor_flags + mentee_flags


def test_mentees_outnumber_mentor_slots(synthetic_cohort):
    """Without this the waitlist and blocking-pair paths are unreachable."""
    _, mentors, mentees, _ = synthetic_cohort
    assert sum(mentor.capacity for mentor in mentors) < len(mentees)


def test_enough_avoid_answers_to_fire_the_constraint(synthetic_cohort):
    questions, mentors, mentees, _ = synthetic_cohort
    avoid = next(q for q in questions if q.role == ROLE_AVOID)
    answered = [
        person
        for person in mentors + mentees
        if person.responses.get(avoid.row, "").strip()
    ]
    assert len(answered) >= 15


def test_locations_span_several_time_zones(synthetic_cohort):
    questions, mentors, mentees, _ = synthetic_cohort
    location = next(q for q in questions if q.role == ROLE_LOCATION)
    stated = {person.responses[location.row] for person in mentors + mentees}
    assert len(stated) >= 10
    assert any("Pacific" in text for text in stated), "one states its own offset"
    assert any("travelling" in text.lower() for text in stated), "one is unresolvable"
