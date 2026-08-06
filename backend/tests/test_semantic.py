"""Tests for the semantic scorer and its percentile calibration."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from app.exports import link_columns, read_export
from app.normalize import normalize
from app.questions import ROLE_SEMANTIC, Question, load_questions
from app.respondents import MENTEE, MENTOR, build_respondents
from app.responses import KIND_BLANK, KIND_TEXT, Response, parse_responses
from app.semantic import calibrate, score_semantic, similarities

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"

ROW = 50


@pytest.fixture
def questions():
    return load_questions(DATABASE)


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


def blank() -> dict[int, Response]:
    return {ROW: Response(row=ROW, kind=KIND_BLANK, text="", indices=(), write_ins=())}


def spread_cache(labels: list[str]) -> dict[str, np.ndarray]:
    """Unit vectors spaced evenly around a circle, giving predictable cosines."""
    angles = np.linspace(0, np.pi / 2, len(labels))
    return {
        normalize(label): np.array([np.cos(angle), np.sin(angle)])
        for label, angle in zip(labels, angles)
    }


def test_similarities_covers_every_answered_pair():
    question = semantic_question()
    cache = spread_cache(["a", "b", "c", "d", "e"])
    mentors = [text("a"), text("b"), text("c")]
    mentees = [text("d"), text("e")]
    assert len(similarities(question, mentors, mentees, cache)) == 6


def test_similarities_skips_pairs_where_one_side_is_blank():
    question = semantic_question()
    cache = spread_cache(["a", "b", "c"])
    mentors = [text("a"), blank()]
    mentees = [text("b"), text("c")]
    assert len(similarities(question, mentors, mentees, cache)) == 2


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


def test_scoring_splits_the_cohort_at_the_cutoffs():
    """The point of calibration: the top slice scores 10, the bottom 0."""
    question = semantic_question()
    cache = spread_cache(["a", "b", "c", "d", "e"])
    mentors = [text("a"), text("b"), text("c")]
    mentees = [text("d"), text("e")]
    cutoffs = calibrate([question], mentors, mentees, cache)

    points = [
        score_semantic(question, mentor[ROW], mentee[ROW], cache, cutoffs)
        for mentor in mentors
        for mentee in mentees
    ]
    assert set(points) <= {0, 5, 10}
    assert 10 in points and 0 in points, "cutoffs must separate the pairs"


def test_identical_answers_score_perfect():
    question = semantic_question()
    cache = spread_cache(["a", "b", "c"])
    mentors = [text("a"), text("b")]
    mentees = [text("a"), text("c")]
    cutoffs = calibrate([question], mentors, mentees, cache)
    assert score_semantic(question, text("a")[ROW], text("a")[ROW], cache, cutoffs) == 10


def test_blank_answer_is_unscorable():
    question = semantic_question()
    cache = spread_cache(["a", "b"])
    mentors, mentees = [text("a")], [text("b")]
    cutoffs = calibrate([question], mentors, mentees, cache)

    assert score_semantic(question, blank()[ROW], text("b")[ROW], cache, cutoffs) is None
    assert score_semantic(question, text("a")[ROW], blank()[ROW], cache, cutoffs) is None


def test_question_nobody_answered_is_dropped():
    question = semantic_question()
    cutoffs = calibrate([question], [blank()], [blank()], {})
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


def test_real_cohort_cutoffs_are_derived_per_question(real_exports, questions):
    """Every semantic question gets its own cutoffs, and they are not identical."""
    from app.embeddings import build_cache

    mentor_frame = read_export(FIXTURES / "mentor_responses.csv")
    mentee_frame = read_export(FIXTURES / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, _ = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, _ = build_respondents(questions, links, mentee_frame, MENTEE)
    mentor_answers = [parse_responses(questions, person) for person in mentors]
    mentee_answers = [parse_responses(questions, person) for person in mentees]
    cache = build_cache(questions, mentor_answers + mentee_answers)

    cutoffs = calibrate(questions, mentor_answers, mentee_answers, cache)
    semantic_rows = {q.row for q in questions if q.role == ROLE_SEMANTIC}

    assert set(cutoffs) <= semantic_rows
    assert len(cutoffs) >= 5, "most semantic questions were answered by both sides"
    assert all(c.upper >= c.lower for c in cutoffs.values())
    assert len({round(c.upper, 3) for c in cutoffs.values()}) > 1, (
        "cutoffs vary by question, which is the reason for calibrating"
    )
