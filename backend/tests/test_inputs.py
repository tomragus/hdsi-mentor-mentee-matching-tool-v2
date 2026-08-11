"""Tests for reading the exports and parsing what people answered."""

import numpy as np
import pandas as pd
import pytest

from app.config import WRITE_IN_PENALTY
from app.inputs import (
    KIND_BLANK,
    KIND_CHECKBOX,
    KIND_CHOICE,
    MENTEE,
    MENTOR,
    ExportLinkError,
    Response,
    build_cache,
    build_respondents,
    embed,
    link_columns,
    missing_email,
    parse_response,
    parse_responses,
    penalty,
    read_export,
    resolve_response,
    resolve_write_ins,
    similarity,
)
from helpers import FEEDBACK_ROW, REAL_MENTEE, REAL_MENTOR, STYLE_ROW, cache_from, stand_in

MENTEE_EMAIL = "UCSD Email Address"
CAPACITY = "How many mentees would you like to be matched with?"


@pytest.fixture(scope="module")
def exports(real_exports):
    return read_export(REAL_MENTOR), read_export(REAL_MENTEE)


@pytest.fixture(scope="module")
def links(questions, exports):
    return link_columns(questions, *exports)


@pytest.fixture(scope="module")
def parsed(questions, exports, links):
    """Every real respondent's parsed answers, keyed by their display name."""
    people = {}
    for frame, side in zip(exports, (MENTOR, MENTEE)):
        for person in build_respondents(questions, links, frame, side):
            # The sample records names as initials.
            people[person.name] = parse_responses(questions, person)
    return people


# --- reading and linking --------------------------------------------------


def test_reads_values_as_text(exports):
    """Graduation years must stay strings, not become integers or floats."""
    assert all(isinstance(v, str) for v in exports[0]["Graduation Year"].dropna())


def test_linking_ignores_column_order(questions, exports):
    """Reordering a form's questions must not change the mapping."""
    mentor, mentee = exports
    expected = link_columns(questions, mentor, mentee)
    assert link_columns(questions, mentor[mentor.columns[::-1]], mentee) == expected


def test_missing_question_aborts_naming_it(questions, exports):
    renamed = exports[1].rename(columns={MENTEE_EMAIL: "Your Email"})
    with pytest.raises(ExportLinkError) as caught:
        link_columns(questions, exports[0], renamed)

    assert MENTEE_EMAIL in str(caught.value)
    assert "mentee" in str(caught.value)


# --- one record per person ------------------------------------------------


def test_email_key_ignores_surrounding_text(questions, links, exports):
    """"[not required] someone@ucsd.edu" must key on the address alone."""
    raw = exports[1][MENTEE_EMAIL].dropna()
    assert any("not required" in cell for cell in raw), "the sample has such a cell"

    mentees = build_respondents(questions, links, exports[1], MENTEE)
    addresses = [m.key for m in mentees if "@" in m.key]
    assert addresses
    assert all(" " not in key and "[" not in key for key in addresses)


def test_missing_email_is_kept_and_findable(questions, links, exports):
    mentees = build_respondents(questions, links, exports[1], MENTEE)
    assert len(mentees) == 4, "a respondent without an email is still matched"
    assert len([m for m in mentees if missing_email(m)]) == 1


@pytest.mark.parametrize(
    "timestamp, capacity, why",
    [
        ("12/31/2026 23:59:59", 1, "the later answer wins"),
        ("01/01/2020 00:00:00", 2, "the original, newer submission is retained"),
    ],
)
def test_a_resubmission_only_wins_when_it_is_newer(
    questions, links, exports, timestamp, capacity, why
):
    resubmission = exports[0].iloc[[0]].copy()
    resubmission["Timestamp"] = timestamp
    resubmission[CAPACITY] = "One"
    combined = pd.concat([exports[0], resubmission], ignore_index=True)

    mentors = build_respondents(questions, links, combined, MENTOR)

    assert len(mentors) == 6, "the resubmission replaces rather than adds"
    assert next(m for m in mentors if m.name == "AG").capacity == capacity, why


# --- parsing answers ------------------------------------------------------


def test_blank_cell_is_no_response(parsed):
    """A skipped optional question must be distinguishable from an answered one."""
    blanks = [
        response
        for answers in parsed.values()
        for response in answers.values()
        if response.kind == KIND_BLANK
    ]
    assert blanks, "the sample exports contain skipped questions"
    assert all(response.indices == () for response in blanks)


def test_differently_worded_options_align_by_index(parsed):
    """Rows 9 and 11 word their options differently on each side.

    Each side is matched against its own option list, so the same choice
    resolves to the same index regardless of wording.
    """
    mentor, mentee = parsed["PS"], parsed["KJ"]
    assert mentor[STYLE_ROW].indices == mentee[STYLE_ROW].indices == (2,)
    assert mentor[FEEDBACK_ROW].indices == mentee[FEEDBACK_ROW].indices == (3,)


def test_no_real_response_is_wrongly_called_a_write_in(parsed):
    """Every write-in in the sample exports is genuine free text.

    A listed option misread as a write-in would cost that pair a 5-point penalty
    it did not earn, which is what this guards.
    """
    assert {
        text
        for answers in parsed.values()
        for response in answers.values()
        for text in response.write_ins
    } == {
        "whatever the mentee needs",
        "Anything but email.",
        "Applications of data science outside of the traditional sense",
        "Communication is one of the most important skills I will adapt to "
        "whatever will resonate with you",
        "Effective Communication Skills",
        "Technical skills",
    }


def test_checkbox_option_containing_a_comma_is_not_split():
    question = stand_in(options=["In Person", "Both, depending on the day", "Email"])
    response = parse_response(question, MENTOR, "In Person, Both, depending on the day")
    assert response.indices == (1, 2)
    assert response.write_ins == ()


# --- embedding and write-ins ----------------------------------------------


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


def test_resolution_adds_the_index_and_keeps_the_text():
    question = stand_in(options=["Resume Review", "How to Network"])
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
    wrote_in = Response(1, KIND_CHOICE, "x", (1,), ("x",))
    listed = Response(1, KIND_CHOICE, "Yes", (1,), ())

    assert penalty(listed, listed) == 0
    assert penalty(wrote_in, listed) == WRITE_IN_PENALTY
    assert penalty(listed, wrote_in) == WRITE_IN_PENALTY
    assert penalty(wrote_in, wrote_in) == WRITE_IN_PENALTY


def test_every_real_write_in_resolves_to_a_listed_option(questions, links, exports, by_row):
    """The sample cohort, parsed and embedded, end to end."""
    groups = [
        (side, [
            parse_responses(questions, person)
            for person in build_respondents(questions, links, frame, side)
        ])
        for frame, side in zip(exports, (MENTOR, MENTEE))
    ]
    cache = build_cache(questions, [a for _, sets in groups for a in sets])
    resolved_count = 0

    for side, answer_sets in groups:
        for answers in answer_sets:
            for row, response in resolve_write_ins(
                questions, side, answers, cache
            ).items():
                if not response.write_ins:
                    continue
                resolved_count += 1
                options = (
                    by_row[row].mentor_options
                    if side == MENTOR
                    else by_row[row].mentee_options
                )
                listed = {o.index for o in options if not o.is_write_in}
                assert set(response.indices) <= listed
                assert response.indices, "a write-in always lands on some option"

    assert resolved_count == 6, "the sample cohort contains six write-ins"
