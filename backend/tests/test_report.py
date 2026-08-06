"""Tests for post-solve reporting."""

from pathlib import Path

import pytest

from app.assignment import solve
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
from app.report import build_report, build_waitlist, find_blocking_pairs
from app.respondents import MENTEE, MENTOR, Respondent

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


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


def test_a_stable_solution_reports_nothing():
    mentors = [participant("m1", MENTOR), participant("m2", MENTOR)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    scores = table(
        {("m1", "e1"): 0.9, ("m1", "e2"): 0.1, ("m2", "e1"): 0.1, ("m2", "e2"): 0.9}
    )
    solution = solve(mentors, mentees, scores)

    assert find_blocking_pairs(mentors, mentees, scores, solution.assignments, set()) == []


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


def test_an_assigned_pair_is_not_a_blocking_pair():
    mentors = [participant("m", MENTOR)]
    mentees = [participant("e", MENTEE)]
    scores = table({("m", "e"): 0.9})
    solution = solve(mentors, mentees, scores)

    assert find_blocking_pairs(mentors, mentees, scores, solution.assignments, set()) == []


def test_a_mentor_with_a_free_slot_has_no_partner_to_give_up():
    """Reported as None rather than as a score, so the display can say so."""
    mentors = [participant("m", MENTOR, capacity=2)]
    mentees = [participant("e1", MENTEE), participant("e2", MENTEE)]
    scores = table({("m", "e1"): 0.9, ("m", "e2"): 0.8})

    # Only e1 exists in the solve, so the mentor keeps a slot free.
    solution = solve(mentors, [mentees[0]], scores)
    found = find_blocking_pairs(mentors, mentees, scores, solution.assignments, set())

    assert len(found) == 1
    assert found[0].mentee_key == "e2"
    assert found[0].mentor_current_percentage is None
    assert found[0].mentee_current_percentage is None


def test_waitlist_is_ordered_by_best_available_score():
    mentees = [participant(f"e{i}", MENTEE) for i in range(3)]
    scores = table({("m", "e0"): 0.4, ("m", "e1"): 0.9, ("m", "e2"): 0.6})

    entries = build_waitlist(mentees, ("e0", "e1", "e2"), scores, set())

    assert [entry.mentee_key for entry in entries] == ["e1", "e2", "e0"]
    assert entries[0].best_percentage == pytest.approx(90)
    assert entries[0].best_mentor_key == "m"


def test_waitlist_ignores_mentors_the_mentee_is_blocked_from():
    """"Best available" has to mean available."""
    mentees = [participant("e", MENTEE)]
    scores = table({("m1", "e"): 0.9, ("m2", "e"): 0.3})

    entries = build_waitlist(mentees, ("e",), scores, {("m1", "e")})

    assert entries[0].best_percentage == pytest.approx(30)
    assert entries[0].best_mentor_key == "m2"


def test_waitlist_only_holds_unassigned_mentees():
    mentees = [participant("e0", MENTEE), participant("e1", MENTEE)]
    scores = table({("m", "e0"): 0.5, ("m", "e1"): 0.5})
    assert [e.mentee_key for e in build_waitlist(mentees, ("e1",), scores, set())] == ["e1"]


# --- against the fixtures ----------------------------------------------------


@pytest.fixture(scope="module")
def questions():
    return load_questions(DATABASE)


def run(directory: Path, questions):
    mentor_frame = read_export(directory / "mentor_responses.csv")
    mentee_frame = read_export(directory / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, mentees, scoring, flags = prepare(questions, links, mentor_frame, mentee_frame)
    scores = score_all(scoring, mentors, mentees)

    people = [p.respondent for p in mentors + mentees]
    avoid_question = next(q for q in questions if q.role == ROLE_AVOID)
    vocabulary = build_vocabulary(questions, people)
    extracted, avoid_flags = extract_avoid_terms(avoid_question, people, vocabulary)
    stated = stated_terms_for_all(questions, people, vocabulary)
    blocks = blocked_pairs(
        [p.respondent for p in mentors], [p.respondent for p in mentees], extracted, stated
    )
    cells = blocked_cells(blocks)
    solution = solve(mentors, mentees, scores, blocked=cells)
    report = build_report(
        mentors,
        mentees,
        scores,
        solution,
        blocks,
        flags + avoid_flags,
        scoring.cutoffs,
        excluded=cells,
    )
    return mentors, mentees, report


@pytest.fixture(scope="module")
def real_report(real_exports, questions):
    return run(FIXTURES, questions)


@pytest.fixture(scope="module")
def synthetic_report(questions):
    return run(SYNTHETIC, questions)


def test_real_report_ranks_matches_by_score(real_report):
    _, _, report = real_report
    percentages = [match.percentage for match in report.matches]
    assert percentages == sorted(percentages, reverse=True)
    assert len(report.matches) == 4
    assert report.waitlist == ()


def test_real_report_carries_everything_set_aside_for_review(real_report):
    _, _, report = real_report
    assert len(report.avoid_blocks) == 1
    assert report.avoid_blocks[0].mentor_triggers == ("finance",)
    assert len(report.cutoffs) == 8, "one per semantic question"
    reasons = " ".join(flag.reason for flag in report.review_flags)
    assert "email" in reasons and "time zone" in reasons


def test_matches_name_both_people(real_report):
    _, _, report = real_report
    assert all(match.mentor_name and match.mentee_name for match in report.matches)
    assert all(match.scored_questions > 0 for match in report.matches)


def test_synthetic_report_waitlists_the_mentees_who_did_not_fit(synthetic_report):
    mentors, mentees, report = synthetic_report
    assert len(report.matches) + len(report.waitlist) == len(mentees)
    assert report.unfilled_slots == 0

    ordering = [entry.best_percentage for entry in report.waitlist]
    assert ordering == sorted(ordering, reverse=True)


def test_synthetic_blocking_pairs_are_genuine(synthetic_report):
    """Each reported pair must actually beat what both sides were given."""
    _, _, report = synthetic_report
    assert report.blocking_pairs, "the contested cohort produces at least one"

    for pair in report.blocking_pairs:
        assert pair.mentor_current_percentage is None or (
            pair.percentage > pair.mentor_current_percentage
        )
        assert pair.mentee_current_percentage is None or (
            pair.percentage > pair.mentee_current_percentage
        )


def test_no_blocking_pair_is_also_an_assigned_pair(synthetic_report):
    _, _, report = synthetic_report
    assigned = {(m.mentor_key, m.mentee_key) for m in report.matches}
    reported = {(b.mentor_key, b.mentee_key) for b in report.blocking_pairs}
    assert not (assigned & reported)


def test_no_avoid_blocked_pair_is_reported_as_blocking(synthetic_report):
    _, _, report = synthetic_report
    blocked = {(b.mentor_key, b.mentee_key) for b in report.avoid_blocks}
    reported = {(b.mentor_key, b.mentee_key) for b in report.blocking_pairs}
    assert not (blocked & reported)
