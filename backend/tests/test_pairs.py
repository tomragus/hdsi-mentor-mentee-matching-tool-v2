"""Tests for pair scoring."""

from pathlib import Path

import pytest

from app.exports import link_columns, read_export
from app.pairs import Participant, ScoringContext, prepare, score_all, score_pair
from app.questions import (
    ROLE_AVOID,
    ROLE_MULTIPLE_CHOICE,
    ROLE_UNSCORED,
    Option,
    Question,
    load_questions,
)
from app.respondents import MENTEE, MENTOR, Respondent
from app.responses import KIND_BLANK, KIND_CHOICE, Response

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


def yes_no_question(row: int, weight: int, role: str = ROLE_MULTIPLE_CHOICE) -> Question:
    """A stand-in row where matching answers score 10 and differing ones 0."""
    options = (
        Option(index=1, text="Yes", is_write_in=False),
        Option(index=2, text="No", is_write_in=False),
    )
    return Question(
        row=row,
        response_type="multiple choice",
        role=role,
        weight=weight,
        mentor_question=f"stand-in {row}",
        mentee_question=f"stand-in {row}",
        mentor_required=True,
        mentee_required=True,
        mentor_options=options,
        mentee_options=options,
        is_natural_language=False,
        percentiles=(85, 50),
        choice_scores={(1, 1): 10, (2, 2): 10, (1, 2): 0, (2, 1): 0},
        overlap_thresholds=None,
    )


def answer(row: int, index: int, write_in: bool = False) -> Response:
    return Response(
        row=row,
        kind=KIND_CHOICE,
        text="Yes" if index == 1 else "No",
        indices=(index,),
        write_ins=("something typed in",) if write_in else (),
    )


def blank(row: int) -> Response:
    return Response(row=row, kind=KIND_BLANK, text="", indices=(), write_ins=())


def participant(key: str, side: str, answers: dict[int, Response]) -> Participant:
    return Participant(
        respondent=Respondent(
            key=key,
            side=side,
            name=key,
            email=key,
            capacity=1,
            submitted_at=None,
            responses={},
        ),
        answers=answers,
    )


def context(questions: list[Question]) -> ScoringContext:
    return ScoringContext(questions=questions, cache={}, cutoffs={}, offsets={})


def test_weight_multiplies_the_points():
    questions = [yes_no_question(1, weight=3)]
    mentor = participant("m", MENTOR, {1: answer(1, 1)})
    mentee = participant("e", MENTEE, {1: answer(1, 1)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.raw == 30
    assert score.maximum == 30
    assert score.percentage == 100


def test_penalty_is_subtracted_after_weighting():
    """A write-in costs the same 5 points whatever the question's weight."""
    questions = [yes_no_question(1, weight=3)]
    mentor = participant("m", MENTOR, {1: answer(1, 1, write_in=True)})
    mentee = participant("e", MENTEE, {1: answer(1, 1)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.raw == 25, "30 weighted points less a flat 5"
    assert score.maximum == 30
    assert score.question_scores[0].penalty == 5


def test_a_skipped_question_leaves_the_ratio_untouched():
    """The point of the optional-question rule: skipping costs nothing."""
    questions = [yes_no_question(1, weight=1), yes_no_question(2, weight=1)]
    both = participant("m", MENTOR, {1: answer(1, 1), 2: answer(2, 1)})
    partial = participant("e", MENTEE, {1: answer(1, 1), 2: blank(2)})

    score = score_pair(context(questions), both, partial)

    assert score.raw == 10
    assert score.maximum == 10, "the skipped question leaves the denominator too"
    assert score.percentage == 100
    assert score.scored_questions == 1


def test_a_disagreement_is_not_the_same_as_a_skip():
    """Zero points still counts toward the denominator; a skip does not."""
    questions = [yes_no_question(1, weight=1), yes_no_question(2, weight=1)]
    mentor = participant("m", MENTOR, {1: answer(1, 1), 2: answer(2, 1)})
    disagrees = participant("e", MENTEE, {1: answer(1, 1), 2: answer(2, 2)})
    skips = participant("e2", MENTEE, {1: answer(1, 1), 2: blank(2)})

    assert score_pair(context(questions), mentor, disagrees).percentage == 50
    assert score_pair(context(questions), mentor, skips).percentage == 100


def test_weight_zero_questions_are_excluded():
    questions = [yes_no_question(1, weight=1), yes_no_question(2, weight=0)]
    mentor = participant("m", MENTOR, {1: answer(1, 1), 2: answer(2, 2)})
    mentee = participant("e", MENTEE, {1: answer(1, 1), 2: answer(2, 1)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.maximum == 10
    assert [s.row for s in score.question_scores] == [1]


def test_the_avoid_question_contributes_nothing():
    """It decides which pairings are allowed, not how well two people fit."""
    questions = [yes_no_question(1, weight=2, role=ROLE_AVOID)]
    mentor = participant("m", MENTOR, {1: answer(1, 1)})
    mentee = participant("e", MENTEE, {1: answer(1, 1)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.question_scores == ()
    assert score.maximum == 0


def test_unscored_rows_are_ignored():
    questions = [yes_no_question(1, weight=2, role=ROLE_UNSCORED)]
    mentor = participant("m", MENTOR, {1: answer(1, 1)})
    mentee = participant("e", MENTEE, {1: answer(1, 1)})
    assert score_pair(context(questions), mentor, mentee).maximum == 0


def test_a_score_may_fall_below_zero():
    """Penalties come off the raw total and nothing clamps the result."""
    questions = [yes_no_question(1, weight=1)]
    mentor = participant("m", MENTOR, {1: answer(1, 1, write_in=True)})
    mentee = participant("e", MENTEE, {1: answer(1, 2)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.raw == -5
    assert score.percentage == -50


def test_a_pair_with_nothing_to_score_is_zero():
    questions = [yes_no_question(1, weight=1)]
    mentor = participant("m", MENTOR, {1: blank(1)})
    mentee = participant("e", MENTEE, {1: blank(1)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.maximum == 0
    assert score.normalized == 0.0, "no division, no crash"


def test_score_all_covers_the_whole_matrix():
    questions = [yes_no_question(1, weight=1)]
    mentors = [participant(f"m{i}", MENTOR, {1: answer(1, 1)}) for i in range(3)]
    mentees = [participant(f"e{i}", MENTEE, {1: answer(1, 1)}) for i in range(4)]

    scores = score_all(context(questions), mentors, mentees)

    assert len(scores) == 12
    assert ("m2", "e3") in scores


# --- against the fixtures ----------------------------------------------------


@pytest.fixture(scope="module")
def questions():
    return load_questions(DATABASE)


def run(directory: Path, questions):
    mentor_frame = read_export(directory / "mentor_responses.csv")
    mentee_frame = read_export(directory / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, mentees, scoring, flags = prepare(questions, links, mentor_frame, mentee_frame)
    return mentors, mentees, score_all(scoring, mentors, mentees), flags


@pytest.fixture(scope="module")
def real_run(real_exports, questions):
    return run(FIXTURES, questions)


@pytest.fixture(scope="module")
def synthetic_run(questions):
    return run(SYNTHETIC, questions)


def test_real_cohort_scores_every_pair(real_run):
    mentors, mentees, scores, _ = real_run
    assert len(scores) == len(mentors) * len(mentees) == 24


def test_real_cohort_scores_are_spread_out(real_run):
    """A scorer that gave everyone the same number would be useless."""
    _, _, scores, _ = real_run
    percentages = [score.percentage for score in scores.values()]
    assert 0 < min(percentages) < max(percentages) <= 100
    assert max(percentages) - min(percentages) > 20


def test_ratios_are_consistent_with_their_parts(synthetic_run):
    _, _, scores, _ = synthetic_run
    for score in scores.values():
        assert score.raw == sum(s.contribution for s in score.question_scores)
        assert score.maximum == sum(s.maximum for s in score.question_scores)
        if score.maximum:
            assert score.normalized == pytest.approx(score.raw / score.maximum)


def test_pairs_differ_in_how_many_questions_they_rest_on(synthetic_run):
    """Thin-evidence pairs exist, which is why the count is kept on the record."""
    _, _, scores, _ = synthetic_run
    counts = {score.scored_questions for score in scores.values()}
    assert len(counts) > 1


def test_prepare_reports_review_flags(real_run):
    _, _, _, flags = real_run
    reasons = " ".join(flag.reason for flag in flags)
    assert "email" in reasons, "the mentee who gave no email"
    assert "time zone" in reasons, "the mentee who did not give a location"
