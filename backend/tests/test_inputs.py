"""Tests for reading the exports and parsing what people answered."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.config import normalize, WRITE_IN_PENALTY
from app.inputs import (
    ExportLinkError,
    KIND_BLANK,
    KIND_CHECKBOX,
    KIND_CHOICE,
    MENTEE,
    MENTOR,
    Option,
    Question,
    Response,
    ROLE_CHECKBOX,
    build_cache,
    build_respondents,
    embed,
    link_columns,
    load_questions,
    missing_email,
    parse_response,
    parse_responses,
    penalty,
    read_export,
    resolve_response,
    resolve_write_ins,
    similarity,
)


FIXTURES = Path(__file__).parent / "fixtures"
DATABASE = Path(__file__).parents[2] / "Mentee_Mentor Questions Database.csv"


@pytest.fixture(scope="module")
def questions():
    return load_questions(DATABASE)


@pytest.fixture
def mentor(real_exports):
    return read_export(FIXTURES / "mentor_responses.csv")


@pytest.fixture
def mentee(real_exports):
    return read_export(FIXTURES / "mentee_responses.csv")


def test_reads_values_as_text(mentor):
    """Graduation years must stay strings, not become integers or floats."""
    assert all(isinstance(v, str) for v in mentor["Graduation Year"].dropna())


def test_linking_ignores_column_order(questions, mentor, mentee):
    """Reordering a form's questions must not change the mapping."""
    expected = link_columns(questions, mentor, mentee)
    shuffled = link_columns(questions, mentor[mentor.columns[::-1]], mentee)
    assert shuffled == expected


def test_missing_question_aborts_naming_it(questions, mentor, mentee):
    renamed = mentee.rename(columns={"UCSD Email Address": "Your Email"})
    with pytest.raises(ExportLinkError) as caught:
        link_columns(questions, mentor, renamed)

    assert "UCSD Email Address" in str(caught.value)
    assert "mentee" in str(caught.value)


MENTEE_EMAIL = "UCSD Email Address"
CAPACITY = "How many mentees would you like to be matched with?"


@pytest.fixture
def frames(real_exports):
    return (
        read_export(FIXTURES / "mentor_responses.csv"),
        read_export(FIXTURES / "mentee_responses.csv"),
    )


@pytest.fixture
def links(questions, frames):
    return link_columns(questions, *frames)


def test_email_key_ignores_surrounding_text(questions, links, frames):
    """"[not required] someone@ucsd.edu" must key on the address alone."""
    raw = frames[1][MENTEE_EMAIL].dropna()
    assert any("not required" in cell for cell in raw), "the sample has such a cell"

    mentees = build_respondents(questions, links, frames[1], MENTEE)
    addresses = [m.key for m in mentees if "@" in m.key]
    assert addresses
    assert all(" " not in key and "[" not in key for key in addresses)


def test_missing_email_is_kept_and_findable(questions, links, frames):
    mentees = build_respondents(questions, links, frames[1], MENTEE)
    assert len(mentees) == 4, "a respondent without an email is still matched"
    assert len([m for m in mentees if missing_email(m)]) == 1


def test_duplicate_submissions_keep_the_latest(questions, links, frames):
    mentor = frames[0]
    resubmission = mentor.iloc[[0]].copy()
    resubmission["Timestamp"] = "12/31/2026 23:59:59"
    resubmission[CAPACITY] = "One"
    combined = pd.concat([mentor, resubmission], ignore_index=True)

    mentors = build_respondents(questions, links, combined, MENTOR)

    assert len(mentors) == 6, "the resubmission replaces rather than adds"
    kept = next(m for m in mentors if m.name == "AG")
    assert kept.capacity == 1, "the later answer wins"


def test_earlier_resubmission_does_not_replace_later(questions, links, frames):
    mentor = frames[0]
    stale = mentor.iloc[[0]].copy()
    stale["Timestamp"] = "01/01/2020 00:00:00"
    stale[CAPACITY] = "One"
    combined = pd.concat([mentor, stale], ignore_index=True)

    mentors = build_respondents(questions, links, combined, MENTOR)
    kept = next(m for m in mentors if m.name == "AG")
    assert kept.capacity == 2, "the original, newer submission is retained"


FEEDBACK_ROW = 9
STYLE_ROW = 11


@pytest.fixture
def parsed(real_exports, questions):
    mentor_frame = read_export(FIXTURES / "mentor_responses.csv")
    mentee_frame = read_export(FIXTURES / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)

    people = {}
    for frame, side in ((mentor_frame, MENTOR), (mentee_frame, MENTEE)):
        respondents = build_respondents(questions, links, frame, side)
        for respondent in respondents:
            # Keyed by display name, which the sample records as initials.
            people[respondent.name] = parse_responses(questions, respondent)
    return people


def checkbox_question(options: list[str]) -> Question:
    """A stand-in checkbox row, for cases the real database has no example of."""
    listed = tuple(
        Option(index=position, text=text, is_write_in=False)
        for position, text in enumerate(options, start=1)
    )
    return Question(
        row=99,
        role=ROLE_CHECKBOX,
        weight=1,
        mentor_question="stand-in",
        mentee_question="stand-in",
        mentor_required=False,
        mentee_required=False,
        mentor_options=listed,
        mentee_options=listed,
        percentiles=(85, 50),
        choice_scores=None,
        overlap_thresholds=(),
    )


def test_blank_cell_is_no_response(questions, parsed):
    """A skipped optional question must be distinguishable from an answered one."""
    blanks = [
        response
        for answers in parsed.values()
        for response in answers.values()
        if response.kind == KIND_BLANK
    ]
    assert blanks, "the sample exports contain skipped questions"
    assert all(response.indices == () for response in blanks)


def test_differently_worded_options_align_by_index(questions, parsed):
    """Rows 9 and 11 word their options differently on each side.

    Each side is matched against its own option list, so the same choice
    resolves to the same index regardless of wording.
    """
    mentor = parsed["PS"]
    mentee = parsed["KJ"]
    assert mentor[STYLE_ROW].indices == (2,)
    assert mentee[STYLE_ROW].indices == (2,)
    assert mentor[FEEDBACK_ROW].indices == (3,)
    assert mentee[FEEDBACK_ROW].indices == (3,)


def test_no_real_response_is_wrongly_called_a_write_in(questions, parsed):
    """Every write-in in the sample exports is genuine free text.

    A listed option misread as a write-in would cost that pair a 5-point
    penalty it did not earn, which is what this guards.
    """
    write_ins = {
        text
        for answers in parsed.values()
        for response in answers.values()
        for text in response.write_ins
    }
    assert write_ins == {
        "whatever the mentee needs",
        "Anything but email.",
        "Applications of data science outside of the traditional sense",
        "Communication is one of the most important skills I will adapt to "
        "whatever will resonate with you",
        "Effective Communication Skills",
        "Technical skills",
    }


def test_checkbox_option_containing_a_comma_is_not_split():
    question = checkbox_question(["In Person", "Both, depending on the day", "Email"])
    response = parse_response(question, MENTOR, "In Person, Both, depending on the day")
    assert response.indices == (1, 2)
    assert response.write_ins == ()


@pytest.fixture(scope="module")
def small_cache():
    """A cache over a few fixed strings, so the model loads once for the module."""
    return embed(["machine learning", "deep learning", "competitive swimming"])


def test_vectors_are_unit_length(small_cache):
    for vector in small_cache.values():
        assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)


def test_uncollected_string_raises(small_cache):
    """A missing vector means collection has a bug, so it must not pass silently."""
    with pytest.raises(KeyError):
        similarity(small_cache, "machine learning", "never collected")


def question_with(options: list[tuple[str, bool]], role: str = ROLE_CHECKBOX) -> Question:
    """A stand-in question whose options can be given known vectors."""
    listed = tuple(
        Option(index=position, text=text, is_write_in=is_write_in)
        for position, (text, is_write_in) in enumerate(options, start=1)
    )
    return Question(
        row=99,
        role=role,
        weight=1,
        mentor_question="stand-in",
        mentee_question="stand-in",
        mentor_required=False,
        mentee_required=False,
        mentor_options=listed,
        mentee_options=listed,
        percentiles=(85, 50),
        choice_scores=None,
        overlap_thresholds=(),
    )


def cache_from(vectors: dict[str, list[float]]) -> dict[str, np.ndarray]:
    """Build a cache of unit vectors by hand, so similarities are exact."""
    return {
        normalize(text): np.array(values, dtype=float) / np.linalg.norm(values)
        for text, values in vectors.items()
    }


def test_resolution_adds_the_index_and_keeps_the_text():
    question = question_with([("Resume Review", False), ("How to Network", False)])
    cache = cache_from(
        {
            "Resume Review": [1.0, 0.0],
            "How to Network": [0.0, 1.0],
            "meeting people": [0.1, 0.9],
        }
    )
    response = Response(
        row=99,
        kind=KIND_CHECKBOX,
        text="Resume Review, meeting people",
        indices=(1,),
        write_ins=("meeting people",),
    )

    resolved = resolve_response(question, MENTOR, response, cache)

    assert resolved.indices == (1, 2)
    assert resolved.write_ins == ("meeting people",), "the penalty still applies"
    assert resolved.text == response.text


def test_penalty_applies_once_regardless_of_side():
    wrote_in = Response(row=1, kind=KIND_CHOICE, text="x", indices=(1,), write_ins=("x",))
    listed = Response(row=1, kind=KIND_CHOICE, text="Yes", indices=(1,), write_ins=())

    assert penalty(listed, listed) == 0
    assert penalty(wrote_in, listed) == WRITE_IN_PENALTY
    assert penalty(listed, wrote_in) == WRITE_IN_PENALTY
    assert penalty(wrote_in, wrote_in) == WRITE_IN_PENALTY


@pytest.fixture(scope="module")
def real_run(real_exports):
    """The sample cohort, parsed and embedded, for an end-to-end check."""
    questions = load_questions(DATABASE)
    mentor_frame = read_export(FIXTURES / "mentor_responses.csv")
    mentee_frame = read_export(FIXTURES / "mentee_responses.csv")
    links = link_columns(questions, mentor_frame, mentee_frame)

    groups = []
    for frame, side in ((mentor_frame, MENTOR), (mentee_frame, MENTEE)):
        respondents = build_respondents(questions, links, frame, side)
        groups.append(
            (side, [parse_responses(questions, person) for person in respondents])
        )
    cache = build_cache(questions, [a for _, sets in groups for a in sets])
    return questions, groups, cache


def test_every_real_write_in_resolves_to_a_listed_option(real_run):
    questions, groups, cache = real_run
    by_row = {question.row: question for question in questions}
    resolved_count = 0

    for side, answer_sets in groups:
        for answers in answer_sets:
            for row, response in resolve_write_ins(
                questions, side, answers, cache
            ).items():
                if not response.write_ins:
                    continue
                resolved_count += 1
                listed = {
                    option.index
                    for option in (
                        by_row[row].mentor_options
                        if side == MENTOR
                        else by_row[row].mentee_options
                    )
                    if not option.is_write_in
                }
                assert set(response.indices) <= listed
                assert response.indices, "a write-in always lands on some option"

    assert resolved_count == 6, "the sample cohort contains six write-ins"
