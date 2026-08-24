"""Tests for the five scorers and for how a pair's score is assembled."""

import numpy as np
import pytest

from app.inputs import ROLE_AVOID, ROLE_MULTIPLE_CHOICE, ROLE_SEMANTIC
from app.matching import (
    LocationOffset,
    ScoringContext,
    calibrate,
    resolve_offset,
    score_checkbox,
    score_city_bonus,
    score_location,
    score_multiple_choice,
    score_pair,
    score_semantic,
    similarities,
)
from helpers import (
    COMMITMENT_ROW,
    COMMUNICATION_ROW,
    MENTEE,
    MENTOR,
    blank,
    checkbox,
    choice,
    participant,
    spread_cache,
    stand_in,
    written,
)

ROW = 50


def semantic_question(percentiles: tuple[int, int] = (85, 50)):
    """A stand-in semantic row, so cutoffs can be checked against known values."""
    return stand_in(
        row=ROW, role=ROLE_SEMANTIC, percentiles=percentiles, overlap_thresholds=None
    )


def yes_no(row: int, weight: int, role: str = ROLE_MULTIPLE_CHOICE):
    """A stand-in row where matching answers score 10 and differing ones 0."""
    return stand_in(
        row=row,
        role=role,
        weight=weight,
        options=["Yes", "No"],
        choice_scores={(1, 1): 10, (2, 2): 10, (1, 2): 0, (2, 1): 0},
        overlap_thresholds=None,
    )


def context(questions, offsets: dict[str, LocationOffset] | None = None) -> ScoringContext:
    return ScoringContext(questions=questions, cache={}, cutoffs={}, offsets=offsets or {})


# --- option-based questions -----------------------------------------------


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


# --- semantic questions ---------------------------------------------------


def answers(value: str | None):
    """One side's answers to the stand-in semantic row, blank when None."""
    return {ROW: blank(ROW) if value is None else written(ROW, value)}


def test_cutoffs_are_the_requested_percentiles():
    question = semantic_question(percentiles=(75, 25))
    cache = spread_cache(["a", "b", "c", "d", "e"])
    mentors = [answers("a"), answers("b"), answers("c")]
    mentees = [answers("d"), answers("e")]

    values = similarities(question, mentors, mentees, cache)
    derived = calibrate([question], mentors, mentees, cache)[ROW]

    assert derived.pair_count == 6
    assert derived.upper == pytest.approx(np.percentile(values, 75))
    assert derived.lower == pytest.approx(np.percentile(values, 25))


def test_question_nobody_answered_is_dropped():
    question = semantic_question()
    cutoffs = calibrate([question], [answers(None)], [answers(None)], {})
    assert ROW not in cutoffs
    assert (
        score_semantic(question, written(ROW, "a"), written(ROW, "b"), {}, cutoffs)
        is None
    )


def test_percentile_pair_changes_how_selective_a_question_is():
    """The CSV stores the policy, so editing one cell must change the outcome."""
    cache = spread_cache(["a", "b", "c", "d", "e"])
    mentors = [answers("a"), answers("b"), answers("c")]
    mentees = [answers("d"), answers("e")]

    def perfect(question):
        cutoffs = calibrate([question], mentors, mentees, cache)
        return sum(
            score_semantic(question, m[ROW], e[ROW], cache, cutoffs) == 10
            for m in mentors
            for e in mentees
        )

    assert perfect(semantic_question((95, 90))) < perfect(semantic_question((20, 10)))


# --- location -------------------------------------------------------------


@pytest.mark.parametrize(
    "answer, expected, why",
    [
        # Someone reporting their own offset knows better than a city lookup.
        ("San Diego, CA (+3)", 3.0, "a stated difference wins over the table"),
        ("San Diego, CA 92122-1234", 0.0, "a zip code is not an offset"),
        # "LA" here is Louisiana, and "OR" and "IN" are ordinary English words.
        ("New Orleans, LA, USA", 2.0, "two-letter codes resolve as whole segments"),
        ("Portland, OR", 0.0, "two-letter codes resolve as whole segments"),
        ("Indianapolis, IN, USA", 3.0, "two-letter codes resolve as whole segments"),
        # Guessing would silently distort a score, so these stay unresolved.
        ("Nowhere in particular or thereabouts", None, "no place name in it"),
        ("Why does this matter? ", None, "unrecognizable"),
        ("Currently travelling", None, "unrecognizable"),
        ("", None, "blank"),
    ],
)
def test_offsets_resolve_only_when_the_answer_is_clear(answer, expected, why):
    offset = resolve_offset(answer)
    assert (None if offset is None else offset.hours) == expected, why


def test_a_stated_difference_is_marked_as_stated():
    assert resolve_offset("San Diego, CA (+3)") == LocationOffset(3.0, "stated")


def test_scoring_bands():
    offsets = {
        name: LocationOffset(hours, "lookup")
        for name, hours in
        (("same", 0), ("one", 1), ("two", 2), ("three", 3), ("far", 13.5))
    }
    assert score_location("same", "same", offsets) == 10
    assert score_location("same", "one", offsets) == 5
    assert score_location("same", "two", offsets) == 5
    assert score_location("same", "three", offsets) == 0
    assert score_location("same", "far", offsets) == 0


# --- the city match bonus --------------------------------------------------


@pytest.mark.parametrize(
    "answer, expected_city, why",
    [
        ("San Diego, CA", "san diego", "city resolves regardless of the state suffix"),
        ("san diego", "san diego", "lowercase matches the same city"),
        ("  SAN DIEGO  ", "san diego", "casing and whitespace do not change the city"),
        ("California", None, "a state-only answer is not a city"),
        ("SD", None, "the two-letter code resolves as South Dakota, not San Diego"),
        ("San Diego, CA (+3)", None, "a stated offset names no place at all"),
    ],
)
def test_resolved_city_matches_the_gazetteers_city_entries(answer, expected_city, why):
    offset = resolve_offset(answer)
    assert offset.city == expected_city, why


def test_score_city_bonus_only_when_both_sides_match_the_same_city():
    offsets = {
        "mentor_sd": LocationOffset(0, "lookup", city="san diego"),
        "mentee_sd": LocationOffset(0, "lookup", city="san diego"),
        "mentee_la": LocationOffset(0, "lookup", city="los angeles"),
        "mentee_state_only": LocationOffset(0, "lookup", city=None),
    }
    assert score_city_bonus("mentor_sd", "mentee_sd", offsets) == 30
    assert score_city_bonus("mentor_sd", "mentee_la", offsets) == 0
    assert score_city_bonus("mentor_sd", "mentee_state_only", offsets) == 0
    assert score_city_bonus("mentor_sd", "nobody", offsets) == 0


# --- assembling one pair's score ------------------------------------------


def test_penalty_is_subtracted_after_weighting():
    """A write-in costs the same 5 points whatever the question's weight."""
    questions = [yes_no(1, weight=3)]
    mentor = participant("m", MENTOR, {1: choice(1, 1, write_in=True)})
    mentee = participant("e", MENTEE, {1: choice(1, 1)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.raw == 25, "30 weighted points less a flat 5"
    assert score.maximum == 30
    assert score.question_scores[0].penalty == 5


def test_a_skipped_question_leaves_the_ratio_untouched():
    """The point of the optional-question rule: skipping costs nothing."""
    questions = [yes_no(1, weight=1), yes_no(2, weight=1)]
    both = participant("m", MENTOR, {1: choice(1, 1), 2: choice(2, 1)})
    partial = participant("e", MENTEE, {1: choice(1, 1), 2: blank(2)})

    score = score_pair(context(questions), both, partial)

    assert score.raw == 10
    assert score.maximum == 10, "the skipped question leaves the denominator too"
    assert score.percentage == 100
    assert score.scored_questions == 1


def test_a_disagreement_is_not_the_same_as_a_skip():
    """Zero points still counts toward the denominator; a skip does not."""
    questions = [yes_no(1, weight=1), yes_no(2, weight=1)]
    mentor = participant("m", MENTOR, {1: choice(1, 1), 2: choice(2, 1)})
    disagrees = participant("e", MENTEE, {1: choice(1, 1), 2: choice(2, 2)})
    skips = participant("e2", MENTEE, {1: choice(1, 1), 2: blank(2)})

    assert score_pair(context(questions), mentor, disagrees).percentage == 50
    assert score_pair(context(questions), mentor, skips).percentage == 100


def test_weight_zero_questions_are_excluded():
    questions = [yes_no(1, weight=1), yes_no(2, weight=0)]
    mentor = participant("m", MENTOR, {1: choice(1, 1), 2: choice(2, 2)})
    mentee = participant("e", MENTEE, {1: choice(1, 1), 2: choice(2, 1)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.maximum == 10
    assert [s.row for s in score.question_scores] == [1]


def test_score_pair_adds_the_city_bonus_to_raw_only():
    """The bonus rides on top of raw, leaving maximum -- and so the denominator
    every other question is measured against -- untouched.
    """
    questions = [yes_no(1, weight=1)]
    mentor = participant("m", MENTOR, {1: choice(1, 1)})
    mentee = participant("e", MENTEE, {1: choice(1, 1)})

    same_city = {
        "m": LocationOffset(0, "lookup", city="san diego"),
        "e": LocationOffset(0, "lookup", city="san diego"),
    }
    different_city = {
        "m": LocationOffset(0, "lookup", city="san diego"),
        "e": LocationOffset(0, "lookup", city="los angeles"),
    }

    bonused = score_pair(context(questions, same_city), mentor, mentee)
    plain = score_pair(context(questions, different_city), mentor, mentee)

    assert bonused.raw == plain.raw + 30
    assert bonused.maximum == plain.maximum == 10


def test_the_avoid_question_contributes_nothing():
    """It decides which pairings are allowed, not how well two people fit."""
    questions = [yes_no(1, weight=2, role=ROLE_AVOID)]
    mentor = participant("m", MENTOR, {1: choice(1, 1)})
    mentee = participant("e", MENTEE, {1: choice(1, 1)})

    score = score_pair(context(questions), mentor, mentee)

    assert score.question_scores == ()
    assert score.maximum == 0
