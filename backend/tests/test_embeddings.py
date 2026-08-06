"""Tests for the embedding cache."""

from pathlib import Path

import numpy as np
import pytest

from app.embeddings import build_cache, collect_texts, embed, similarity
from app.exports import link_columns, read_export
from app.normalize import normalize
from app.questions import ROLE_SEMANTIC, load_questions
from app.respondents import MENTEE, MENTOR, build_respondents
from app.responses import parse_responses

FIXTURES = Path(__file__).parent / "fixtures"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"

TOPICS_ROW = 3


@pytest.fixture
def questions():
    return load_questions(DATABASE)


@pytest.fixture
def answer_sets(real_exports, questions):
    mentor_frame = read_export(FIXTURES / "mentor_responses.csv")
    mentee_frame = read_export(FIXTURES / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)

    sets = []
    for frame, side in ((mentor_frame, MENTOR), (mentee_frame, MENTEE)):
        respondents, _ = build_respondents(questions, links, frame, side)
        sets.extend(parse_responses(questions, respondent) for respondent in respondents)
    return sets


@pytest.fixture(scope="module")
def small_cache():
    """A cache over a few fixed strings, so the model loads once for the module."""
    return embed(["machine learning", "deep learning", "competitive swimming"])


def test_collects_semantic_responses(questions, answer_sets):
    texts = collect_texts(questions, answer_sets)
    industry = next(
        q for q in questions if q.mentor_question.startswith("In a word or two")
    )
    assert industry.role == ROLE_SEMANTIC
    answered = {
        normalize(answers[industry.row].text)
        for answers in answer_sets
        if answers[industry.row].text
    }
    assert answered, "the sample exports answer the industry question"
    assert answered <= set(texts)


def test_collects_write_ins_and_their_options(questions, answer_sets):
    """Resolving a write-in means comparing it against that row's options."""
    texts = set(collect_texts(questions, answer_sets))
    assert "whatever the mentee needs" in texts
    topics = next(q for q in questions if q.row == TOPICS_ROW)
    listed = {normalize(o.text) for o in topics.mentor_options if not o.is_write_in}
    assert listed <= texts


def test_excludes_blanks_and_deduplicates(questions, answer_sets):
    texts = collect_texts(questions, answer_sets)
    assert "" not in texts
    assert len(texts) == len(set(texts))
    assert texts == sorted(texts), "order is fixed so runs are reproducible"


def test_option_texts_of_clean_rows_are_not_embedded(questions, answer_sets):
    """No write-in on a row means its options never need vectors."""
    texts = set(collect_texts(questions, answer_sets))
    commitment = next(q for q in questions if q.row == 1)
    assert normalize(commitment.mentor_options[0].text) not in texts


def test_vectors_are_unit_length(small_cache):
    for vector in small_cache.values():
        assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)


def test_identical_text_scores_one(small_cache):
    assert similarity(small_cache, "Machine Learning", "machine learning") == pytest.approx(
        1.0, abs=1e-5
    )


def test_related_text_scores_above_unrelated(small_cache):
    related = similarity(small_cache, "machine learning", "deep learning")
    unrelated = similarity(small_cache, "machine learning", "competitive swimming")
    assert related > unrelated


def test_blank_response_scores_zero(small_cache):
    assert similarity(small_cache, "", "machine learning") == 0.0


def test_uncollected_string_raises(small_cache):
    """A missing vector means collection has a bug, so it must not pass silently."""
    with pytest.raises(KeyError):
        similarity(small_cache, "machine learning", "never collected")


def test_cache_covers_every_collected_string(questions, answer_sets):
    cache = build_cache(questions, answer_sets)
    assert set(cache) == set(collect_texts(questions, answer_sets))
