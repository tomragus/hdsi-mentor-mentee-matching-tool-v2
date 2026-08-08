"""Tests for the five scorers and for how a pair's score is assembled."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from app.inputs import MENTEE, MENTOR, Respondent, link_columns, read_export
from app.matching import Participant, ScoringContext, prepare, score_all, score_pair
from app.normalize import normalize
from app.questions import (
    ROLE_AVOID,
    ROLE_LOCATION,
    ROLE_MULTIPLE_CHOICE,
    ROLE_SEMANTIC,
    Option,
    Question,
    load_questions,
)
from app.responses import KIND_BLANK, KIND_CHECKBOX, KIND_CHOICE, KIND_TEXT, Response
from app.scoring import (
    LocationOffset,
    calibrate,
    resolve_offset,
    score_checkbox,
    score_location,
    score_multiple_choice,
    score_semantic,
    similarities,
)


DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


COMMITMENT_ROW = 1


COMMUNICATION_ROW = 2


@pytest.fixture(scope="module")
def questions():
    return load_questions(DATABASE)


@pytest.fixture
def by_row(questions):
    return {question.row: question for question in questions}


def choice(row: int, index: int) -> Response:
    return Response(row=row, kind=KIND_CHOICE, text="x", indices=(index,), write_ins=())


def checkbox(row: int, *indices: int) -> Response:
    return Response(
        row=row, kind=KIND_CHECKBOX, text="x", indices=tuple(indices), write_ins=()
    )


def blank(row: int) -> Response:
    return Response(row=row, kind=KIND_BLANK, text="", indices=(), write_ins=())


def test_combinations_count_in_either_order(by_row):
    question = by_row[COMMITMENT_ROW]
    for mentor in (1, 2, 3):
        for mentee in (1, 2, 3):
            forward = score_multiple_choice(question, choice(1, mentor), choice(1, mentee))
            backward = score_multiple_choice(question, choice(1, mentee), choice(1, mentor))
            assert forward == backward


def test_checkbox_scores_on_overlap_count(by_row):
    question = by_row[COMMUNICATION_ROW]
    assert score_checkbox(question, checkbox(2, 1, 2, 3), checkbox(2, 1, 2, 3)) == 10
    assert score_checkbox(question, checkbox(2, 1, 2), checkbox(2, 2, 5)) == 5
    assert score_checkbox(question, checkbox(2, 1, 2), checkbox(2, 4, 5)) == 0


def test_blank_on_either_side_is_unscorable(by_row):
    """The optional-question rule needs "no score" told apart from "zero"."""
    question = by_row[COMMITMENT_ROW]
    assert score_multiple_choice(question, blank(1), choice(1, 1)) is None
    assert score_multiple_choice(question, choice(1, 1), blank(1)) is None
    assert score_checkbox(by_row[COMMUNICATION_ROW], blank(2), checkbox(2, 1)) is None


ROW = 50


def semantic_question(percentiles: tuple[int, int] = (85, 50)) -> Question:
    """A stand-in semantic row, so cutoffs can be checked against known values."""
    return Question(
        row=ROW,
        response_type="short answer",
        role=ROLE_SEMANTIC,
        weight=1,
        mentor_question="stand-in",
        mentee_question="stand-in",
        mentor_required=False,
        mentee_required=False,
        mentor_options=(),
        mentee_options=(),
        is_natural_language=True,
        percentiles=percentiles,
        choice_scores=None,
        overlap_thresholds=None,
    )


def text(value: str) -> dict[int, Response]:
    return {ROW: Response(row=ROW, kind=KIND_TEXT, text=value, indices=(), write_ins=())}


def blank_answers() -> dict[int, Response]:
    return {ROW: Response(row=ROW, kind=KIND_BLANK, text="", indices=(), write_ins=())}


def spread_cache(labels: list[str]) -> dict[str, np.ndarray]:
    """Unit vectors spaced evenly around a circle, giving predictable cosines."""
    angles = np.linspace(0, np.pi / 2, len(labels))
    return {
        normalize(label): np.array([np.cos(angle), np.sin(angle)])
        for label, angle in zip(labels, angles)
    }


def test_cutoffs_are_the_requested_percentiles():
    question = semantic_question(percentiles=(75, 25))
    cache = spread_cache(["a", "b", "c", "d", "e"])
    mentors = [text("a"), text("b"), text("c")]
    mentees = [text("d"), text("e")]

    values = similarities(question, mentors, mentees, cache)
    derived = calibrate([question], mentors, mentees, cache)[ROW]

    assert derived.pair_count == 6
    assert derived.upper == pytest.approx(np.percentile(values, 75))
    assert derived.lower == pytest.approx(np.percentile(values, 25))


def test_question_nobody_answered_is_dropped():
    question = semantic_question()
    cutoffs = calibrate([question], [blank_answers()], [blank_answers()], {})
    assert ROW not in cutoffs
    assert score_semantic(question, text("a")[ROW], text("b")[ROW], {}, cutoffs) is None


def test_percentile_pair_changes_how_selective_a_question_is():
    """The CSV stores the policy, so editing one cell must change the outcome."""
    cache = spread_cache(["a", "b", "c", "d", "e"])
    mentors = [text("a"), text("b"), text("c")]
    mentees = [text("d"), text("e")]

    strict = semantic_question(percentiles=(95, 90))
    lenient = replace(strict, percentiles=(20, 10))
    strict_cutoffs = calibrate([strict], mentors, mentees, cache)
    lenient_cutoffs = calibrate([lenient], mentors, mentees, cache)

    def perfect(question, cutoffs):
        return sum(
            score_semantic(question, m[ROW], e[ROW], cache, cutoffs) == 10
            for m in mentors
            for e in mentees
        )

    assert perfect(strict, strict_cutoffs) < perfect(lenient, lenient_cutoffs)


ROOT = Path(__file__).parents[2]


def hours(text: str) -> float | None:
    offset = resolve_offset(text)
    return None if offset is None else offset.hours


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


FIXTURES = Path(__file__).parent / "fixtures"


SYNTHETIC_MENTOR = ROOT / "Synthetic Alumni Mentor Questionnaire (Responses).csv"


SYNTHETIC_MENTEE = ROOT / "Synthetic Student Mentee Questionnaire (Responses).csv"


REAL_MENTOR = FIXTURES / "mentor_responses.csv"


REAL_MENTEE = FIXTURES / "mentee_responses.csv"


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
