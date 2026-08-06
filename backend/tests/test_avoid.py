"""Tests for avoid-question extraction."""

from pathlib import Path

import pytest

from app.avoid import (
    apply_overrides,
    blocked_cells,
    blocked_pairs,
    build_vocabulary,
    extract_avoid_terms,
    is_null_answer,
    keyword_extractor,
    stated_terms,
    stated_terms_for_all,
)
from app.exports import link_columns, read_export
from app.questions import ROLE_AVOID, load_questions
from app.respondents import MENTEE, MENTOR, Respondent, build_respondents
from app.responses import Response
from app.scorers import score_options

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


@pytest.fixture(scope="module")
def questions():
    return load_questions(DATABASE)


def cohort(directory: Path, questions):
    mentor_frame = read_export(directory / "mentor_responses.csv")
    mentee_frame = read_export(directory / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, _ = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees, _ = build_respondents(questions, links, mentee_frame, MENTEE)
    return mentors + mentees


@pytest.fixture(scope="module")
def real_people(real_exports, questions):
    return cohort(FIXTURES, questions)


@pytest.fixture(scope="module")
def synthetic_people(questions):
    return cohort(SYNTHETIC, questions)


@pytest.fixture
def avoid_question(questions):
    return next(q for q in questions if q.role == ROLE_AVOID)


def test_vocabulary_includes_the_examples_the_questions_give(questions, real_people):
    """Row 14 names PySpark and Tableau in its own text, so both are terms."""
    vocabulary = build_vocabulary(questions, real_people)
    assert "pyspark" in vocabulary
    assert "tableau" in vocabulary
    assert "causal inference" in vocabulary


def test_vocabulary_includes_what_people_actually_wrote(questions, real_people):
    vocabulary = build_vocabulary(questions, real_people)
    assert "snowflake" in vocabulary
    assert "epidemiology" in vocabulary


def test_vocabulary_drops_terms_too_generic_to_match_on(questions, real_people):
    """"R" and "AI" would match nearly everyone and block whole cohorts."""
    vocabulary = build_vocabulary(questions, real_people)
    assert "r" not in vocabulary
    assert "ai" not in vocabulary
    assert "data" not in vocabulary
    assert "etc" not in vocabulary


def test_vocabulary_drops_survey_feedback_left_in_an_answer(questions, real_people):
    """One respondent answered these questions with a question of their own."""
    vocabulary = build_vocabulary(questions, real_people)
    assert not any("different than industry" in term for term in vocabulary)
    assert all(len(term.split()) <= 4 for term in vocabulary)


def test_null_answers_are_recognized():
    for text in ("", "None", "n/a", "NA", "no", "nope", " - ", "Not really", "none."):
        assert is_null_answer(text), text
    assert not is_null_answer("No topics in finance please")


def test_keyword_extractor_matches_whole_words_only():
    vocabulary = ("finance", "computer vision")
    assert keyword_extractor("prefer to avoid finance", vocabulary) == {"finance"}
    assert keyword_extractor("I work in refinancing", vocabulary) == set()


def test_keyword_extractor_finds_multi_word_terms():
    vocabulary = ("computer vision", "natural language processing")
    found = keyword_extractor(
        "I would rather not discuss computer vision, it is not my area.", vocabulary
    )
    assert found == {"computer vision"}


def test_extraction_skips_null_and_blank_answers(questions, synthetic_people, avoid_question):
    vocabulary = build_vocabulary(questions, synthetic_people)
    extracted, flags = extract_avoid_terms(avoid_question, synthetic_people, vocabulary)

    answered = [
        person
        for person in synthetic_people
        if person.responses.get(avoid_question.row, "").strip()
    ]
    assert len(extracted) < len(answered), "null answers produce no terms"
    assert not flags


def test_prose_with_no_recognized_terms_has_no_effect(questions, real_people, avoid_question):
    """"Someone who is introverted" is a real answer that resolves to nothing."""
    vocabulary = build_vocabulary(questions, real_people)
    extracted, flags = extract_avoid_terms(avoid_question, real_people, vocabulary)

    introverted = next(
        person
        for person in real_people
        if "introverted" in person.responses.get(avoid_question.row, "")
    )
    assert introverted.key not in extracted
    assert not flags, "an answer with no terms is not a review case"


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


def test_real_cohort_extraction(questions, real_people, avoid_question):
    vocabulary = build_vocabulary(questions, real_people)
    extracted, flags = extract_avoid_terms(avoid_question, real_people, vocabulary)

    names = {person.key: person.name for person in real_people}
    assert not flags
    assert {names[key]: terms for key, terms in extracted.items()} == {"AG": {"finance"}}


def test_synthetic_cohort_extracts_enough_to_block_on(
    questions, synthetic_people, avoid_question
):
    """The real sample barely exercises this, so coverage comes from here."""
    vocabulary = build_vocabulary(questions, synthetic_people)
    extracted, _ = extract_avoid_terms(avoid_question, synthetic_people, vocabulary)

    assert len(extracted) >= 8
    assert {person.side for person in synthetic_people if person.key in extracted} == {
        MENTOR,
        MENTEE,
    }
    assert any(len(terms) > 1 for terms in extracted.values())


# --- Step 13: the hard constraint -------------------------------------------


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


def test_stated_terms_reads_only_the_vocabulary_questions(questions, real_people):
    vocabulary = build_vocabulary(questions, real_people)
    someone = next(p for p in real_people if p.name == "AK")
    terms = stated_terms(questions, someone, vocabulary)

    assert "snowflake" in terms, "from the tools question"
    assert "data engineering" in terms, "from the sub-domains question"
    assert terms <= set(vocabulary), "nothing outside the closed vocabulary"


def test_a_mentors_preference_blocks_the_pair():
    mentor = person("m", MENTOR)
    mentee = person("e", MENTEE)
    blocks = blocked_pairs(
        [mentor], [mentee], {"m": {"finance"}}, {"e": {"finance", "python"}}
    )
    assert len(blocks) == 1
    assert blocks[0].mentor_triggers == ("finance",)
    assert blocks[0].mentee_triggers == ()


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


def test_both_sides_can_trigger_the_same_block():
    blocks = blocked_pairs(
        [person("m", MENTOR)],
        [person("e", MENTEE)],
        {"m": {"finance"}, "e": {"gaming"}},
        {"m": {"gaming"}, "e": {"finance"}},
    )
    assert blocks[0].mentor_triggers == ("finance",)
    assert blocks[0].mentee_triggers == ("gaming",)


def test_no_overlap_means_no_block():
    blocks = blocked_pairs(
        [person("m", MENTOR)], [person("e", MENTEE)], {"m": {"finance"}}, {"e": {"biotech"}}
    )
    assert blocks == []


def test_matching_is_exact_not_partial():
    """A closed vocabulary is what keeps this from firing on loose similarity."""
    blocks = blocked_pairs(
        [person("m", MENTOR)],
        [person("e", MENTEE)],
        {"m": {"investment banking"}},
        {"e": {"banking"}},
    )
    assert blocks == []


def test_a_coordinator_can_override_a_block():
    blocks = blocked_pairs(
        [person("m1", MENTOR), person("m2", MENTOR)],
        [person("e", MENTEE)],
        {"m1": {"finance"}, "m2": {"finance"}},
        {"e": {"finance"}},
    )
    assert len(blocks) == 2

    remaining = apply_overrides(blocks, [("m1", "e")])
    assert blocked_cells(remaining) == {("m2", "e")}


def test_the_avoid_question_awards_no_points(questions, avoid_question):
    """It is a constraint, not a scored question."""
    answer = Response(
        row=avoid_question.row, kind="text", text="finance", indices=(), write_ins=()
    )
    assert score_options(avoid_question, answer, answer) is None


def test_real_cohort_blocks_the_one_pair_it_should(questions, real_people, avoid_question):
    mentors = [p for p in real_people if p.side == MENTOR]
    mentees = [p for p in real_people if p.side == MENTEE]
    vocabulary = build_vocabulary(questions, real_people)
    extracted, _ = extract_avoid_terms(avoid_question, real_people, vocabulary)
    stated = stated_terms_for_all(questions, real_people, vocabulary)

    blocks = blocked_pairs(mentors, mentees, extracted, stated)

    names = {person.key: person.name for person in real_people}
    assert len(blocks) == 1
    assert names[blocks[0].mentor_key] == "AG"
    assert blocks[0].mentor_triggers == ("finance",)


def test_synthetic_cohort_blocks_rarely_and_in_both_directions(
    questions, synthetic_people, avoid_question
):
    mentors = [p for p in synthetic_people if p.side == MENTOR]
    mentees = [p for p in synthetic_people if p.side == MENTEE]
    vocabulary = build_vocabulary(questions, synthetic_people)
    extracted, _ = extract_avoid_terms(avoid_question, synthetic_people, vocabulary)
    stated = stated_terms_for_all(questions, synthetic_people, vocabulary)

    blocks = blocked_pairs(mentors, mentees, extracted, stated)
    total = len(mentors) * len(mentees)

    assert 0 < len(blocks) < total * 0.05, "a hard block should stay rare"
    assert any(block.mentor_triggers for block in blocks)
    assert any(block.mentee_triggers for block in blocks)
    assert len(blocked_cells(blocks)) == len(blocks), "one cell per blocked pair"
