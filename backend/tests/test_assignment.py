"""Tests for the global assignment."""

from pathlib import Path

import pytest

from app.assignment import build_slots, solve
from app.avoid import (
    blocked_cells,
    blocked_pairs,
    build_vocabulary,
    extract_avoid_terms,
    stated_terms_for_all,
)
from app.exports import link_columns, read_export
from app.pairs import PairScore, Participant, prepare, score_all
from app.questions import ROLE_AVOID, load_questions
from app.respondents import MENTEE, MENTOR, Respondent

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


def participant(key: str, side: str, capacity: int = 1) -> Participant:
    return Participant(
        respondent=Respondent(
            key=key,
            side=side,
            name=key,
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


def test_a_mentor_taking_two_mentees_gets_two_slots():
    mentors = [participant("m1", MENTOR, capacity=2), participant("m2", MENTOR)]
    slots = build_slots(mentors)
    assert [slot.mentor_key for slot in slots] == ["m1", "m1", "m2"]


def test_a_mentor_with_capacity_two_can_be_assigned_twice():
    mentors = [participant("m", MENTOR, capacity=2)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    scores = table({("m", "e1"): 0.8, ("m", "e2"): 0.6})

    solution = solve(mentors, mentees, scores)

    assert len(solution.assignments) == 2
    assert {a.mentee_key for a in solution.assignments} == {"e1", "e2"}


def test_surplus_slots_are_left_unfilled():
    mentors = [participant("m1", MENTOR, capacity=2), participant("m2", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m1", "e"): 0.9, ("m2", "e"): 0.4})

    solution = solve(mentors, mentees, scores)

    assert len(solution.assignments) == 1
    assert solution.unfilled_slots == 2
    assert solution.unassigned == ()


def test_mentees_who_cannot_be_placed_are_waitlisted():
    mentors = [participant("m", MENTOR)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    scores = table({("m", "e1"): 0.9, ("m", "e2"): 0.5})

    solution = solve(mentors, mentees, scores)

    assert len(solution.assignments) == 1
    assert solution.unassigned == ("e2",)


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


def test_a_forbidden_pair_behaves_like_a_blocked_one():
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m1", "e"): 0.9, ("m2", "e"): 0.1})

    solution = solve(mentors, mentees, scores, forbidden={("m1", "e")})

    assert solution.assignments[0].mentor_key == "m2"


def test_a_pin_overrides_the_best_available_score():
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m1", "e"): 0.9, ("m2", "e"): 0.1})

    solution = solve(mentors, mentees, scores, pinned={("m2", "e")})

    assert solution.assignments[0].mentor_key == "m2"
    assert solution.assignments[0].score.normalized == 0.1, "the reported score is real"


def test_overriding_a_block_restores_the_pairing():
    """What a coordinator does after reviewing a blocked pair."""
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m1", "e"): 0.9, ("m2", "e"): 0.1})

    blocked = solve(mentors, mentees, scores, blocked={("m1", "e")})
    overridden = solve(mentors, mentees, scores, blocked=set())

    assert blocked.assignments[0].mentor_key == "m2"
    assert overridden.assignments[0].mentor_key == "m1"


def test_exact_ties_resolve_the_same_way_every_run():
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m1", "e"): 0.5, ("m2", "e"): 0.5})

    first = solve(mentors, mentees, scores)
    second = solve(mentors, mentees, scores)

    assert first.assignments == second.assignments


def test_no_mentors_or_no_mentees_is_not_an_error():
    assert solve([], [participant("e", MENTEE)], {}).unassigned == ("e",)
    assert solve([participant("m", MENTOR)], [], {}).assignments == ()


def test_results_are_ranked_by_score():
    mentors = [participant(f"m{i}", MENTOR) for i in range(3)]
    mentees = [participant(f"e{i}", MENTEE) for i in range(3)]
    scores = table(
        {(f"m{i}", f"e{j}"): 0.1 * (i + 1) * (j + 1) for i in range(3) for j in range(3)}
    )

    solution = solve(mentors, mentees, scores)
    values = [a.score.normalized for a in solution.assignments]

    assert values == sorted(values, reverse=True)


# --- against the fixtures ----------------------------------------------------


@pytest.fixture(scope="module")
def questions():
    return load_questions(DATABASE)


def run(directory: Path, questions):
    mentor_frame = read_export(directory / "mentor_responses.csv")
    mentee_frame = read_export(directory / "mentee_responses.csv")
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
def real_run(real_exports, questions):
    return run(FIXTURES, questions)


@pytest.fixture(scope="module")
def synthetic_run(questions):
    return run(SYNTHETIC, questions)


def test_real_cohort_places_everyone(real_run):
    """Nine slots for four mentees, so nobody should be left out."""
    mentors, mentees, scores, blocked = real_run
    solution = solve(mentors, mentees, scores, blocked=blocked)

    assert len(solution.assignments) == 4
    assert solution.unassigned == ()
    assert solution.unfilled_slots == 5


def test_real_cohort_respects_the_avoid_block(real_run):
    mentors, mentees, scores, blocked = real_run
    solution = solve(mentors, mentees, scores, blocked=blocked)
    assigned = {(a.mentor_key, a.mentee_key) for a in solution.assignments}
    assert not (assigned & blocked)


def test_synthetic_cohort_fills_every_slot_and_waitlists_the_rest(synthetic_run):
    mentors, mentees, scores, blocked = synthetic_run
    slots = len(build_slots(mentors))
    solution = solve(mentors, mentees, scores, blocked=blocked)

    assert len(solution.assignments) == slots
    assert solution.unfilled_slots == 0
    assert len(solution.unassigned) == len(mentees) - slots


def test_no_mentee_is_assigned_twice(synthetic_run):
    mentors, mentees, scores, blocked = synthetic_run
    solution = solve(mentors, mentees, scores, blocked=blocked)
    keys = [a.mentee_key for a in solution.assignments]
    assert len(keys) == len(set(keys))


def test_no_mentor_exceeds_their_stated_capacity(synthetic_run):
    mentors, mentees, scores, blocked = synthetic_run
    solution = solve(mentors, mentees, scores, blocked=blocked)

    capacity = {m.respondent.key: max(1, m.respondent.capacity) for m in mentors}
    taken: dict[str, int] = {}
    for assignment in solution.assignments:
        taken[assignment.mentor_key] = taken.get(assignment.mentor_key, 0) + 1
    assert all(count <= capacity[key] for key, count in taken.items())


def test_the_solve_is_at_least_as_good_as_greedy(synthetic_run):
    """Guaranteed by the algorithm, checked here against the real fixture."""
    mentors, mentees, scores, blocked = synthetic_run
    solution = solve(mentors, mentees, scores, blocked=blocked)
    total = sum(a.score.normalized for a in solution.assignments)

    capacity = {m.respondent.key: max(1, m.respondent.capacity) for m in mentors}
    taken: dict[str, int] = {}
    placed: set[str] = set()
    greedy = 0.0
    for score in sorted(scores.values(), key=lambda s: -s.normalized):
        pair = (score.mentor_key, score.mentee_key)
        if pair in blocked or score.mentee_key in placed:
            continue
        if taken.get(score.mentor_key, 0) >= capacity[score.mentor_key]:
            continue
        taken[score.mentor_key] = taken.get(score.mentor_key, 0) + 1
        placed.add(score.mentee_key)
        greedy += score.normalized

    assert total >= greedy
