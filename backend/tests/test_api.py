"""Tests for the HTTP surface."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.config import COMMITMENT_QUESTION_PREFIX, QUESTIONS_DATABASE
from app.inputs import MENTEE, MENTOR, link_columns, load_questions, read_export
from app.main import app
from app.matching import disqualified_by_commitment, find_question, prepare
from helpers import (
    REAL_MENTEE, REAL_MENTOR, SYNTHETIC_MENTEE, SYNTHETIC_MENTOR, participant, score_table,
)


def uploads(mentor_path: Path, mentee_path: Path):
    return {
        "mentor_file": ("mentor.csv", mentor_path.read_bytes()),
        "mentee_file": ("mentee.csv", mentee_path.read_bytes()),
    }


@pytest.fixture
def client():
    # No shared state to reset any more: a fresh TestClient has its own cookie jar,
    # so it gets its own session the moment it makes its first request.
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def ran(real_exports):
    """One real run, shared, since scoring loads the embedding model. Its own
    TestClient, so its own cookie jar and its own isolated session -- other
    fixtures' uploads can never switch this cohort out from under it.
    """
    with TestClient(app) as test_client:
        test_client.post("/api/upload", files=uploads(REAL_MENTOR, REAL_MENTEE))
        yield test_client, test_client.post("/api/run").json()


@pytest.fixture(scope="module")
def synthetic_ran(synthetic_exports):
    """Cohort A: unlike the real cohort, it has people who answered "No" to the
    commitment question, so this is the fixture the disqualification tests need.
    Its own TestClient, same as `ran`, so the two never share a session.
    """
    with TestClient(app) as test_client:
        test_client.post("/api/upload", files=uploads(SYNTHETIC_MENTOR, SYNTHETIC_MENTEE))
        yield test_client, test_client.post("/api/run").json()


# --- uploads that cannot be used ------------------------------------------


def test_upload_names_the_questions_it_could_not_find(real_exports, client):
    """The whole point of the link error is saying which question is missing."""
    broken = REAL_MENTOR.read_text().splitlines()
    broken[0] = broken[0].replace("Graduation Year", "Grad Year")
    response = client.post(
        "/api/upload",
        files={
            "mentor_file": ("mentor.csv", "\n".join(broken).encode()),
            "mentee_file": ("mentee.csv", REAL_MENTEE.read_bytes()),
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert any("Graduation Year" in item["question"] for item in detail["missing"])


@pytest.mark.parametrize(
    "filename, content",
    [
        # Not text at all, under a name that sends it to the CSV reader.
        ("export.csv", bytes([0x89, 0x50, 0x4E, 0x47, 0xFF, 0xFE, 0x00])),
        # Text, under a name that sends it to the Excel reader.
        ("export.xlsx", b"Timestamp,Name\n2026-01-01,AG\n"),
    ],
)
def test_an_unreadable_upload_is_a_bad_request(client, filename, content):
    """This used to look like an outage.

    An uncaught parse failure answers 500 with no JSON body, and the client
    reads exactly that as the backend not running -- so the status matters here
    as much as the wording.
    """
    response = client.post(
        "/api/upload",
        files={
            "mentor_file": (filename, content),
            "mentee_file": ("mentee.csv", b"Timestamp\n"),
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Incorrect file type" in detail
    assert filename in detail, "two files are uploaded, so it names the one at fault"


def test_rows_that_outrun_the_header_ask_for_a_tidy_up(client):
    """A readable sheet with junk past the last column is worth going back to.

    Told apart from the wrong file entirely, since the advice differs.
    """
    response = client.post(
        "/api/upload",
        files={
            "mentor_file": ("mentor.csv", b"One,Two,Three\n1,2,3\n1,2,3,4,5\n"),
            "mentee_file": ("mentee.csv", b"Timestamp\n"),
        },
    )

    assert response.status_code == 400
    assert "formatting issues" in response.json()["detail"]


def test_uploads_the_wrong_way_round_are_read_swapped(real_exports, client):
    """The likeliest mistake there is, and it used to answer with every question.

    The two forms word most questions differently, so a pair that will not link
    one way round and links cleanly the other way round is the same two files in
    the wrong two boxes.
    """
    response = client.post("/api/upload", files=uploads(REAL_MENTEE, REAL_MENTOR))

    assert response.status_code == 200
    assert response.json()["swapped"] is True
    # Read the other way round, so the run behind it is the correct one.
    assert client.post("/api/run").json()["matches"], "the swapped pair still solves"


def test_a_correct_pair_is_not_reported_as_swapped(real_exports, client):
    """The swap has to be detected, not guessed at, or it would fire on a good
    upload and quietly reverse it."""
    response = client.post("/api/upload", files=uploads(REAL_MENTOR, REAL_MENTEE))

    assert response.status_code == 200
    assert response.json()["swapped"] is False


# --- reading a run --------------------------------------------------------


def test_run_returns_the_whole_report(ran):
    _, report = ran
    assert len(report["matches"]) == 4
    assert report["waitlist"] == []
    # Six mentors for four mentees, so everybody is capped at one and the four
    # go to four different mentors, leaving two with nobody.
    assert len(report["unmatched_mentors"]) == 2
    # A missing email is the only thing raised for review.
    assert report["review_flags"]
    assert all("no email address" in flag["reason"] for flag in report["review_flags"])


def test_capacity_travels_with_every_mentor(ran):
    """The manual area enforces capacity, and picks mentors up from both lists."""
    _, report = ran
    assert all(match["mentor_capacity"] >= 1 for match in report["matches"])
    assert all(mentor["capacity"] >= 1 for mentor in report["unmatched_mentors"])


def addresses(report: dict) -> dict[str, str]:
    """Every key in the report, with the address it came with."""
    found = {m["mentor_key"]: m["mentor_email"] for m in report["matches"]}
    found.update({m["mentee_key"]: m["mentee_email"] for m in report["matches"]})
    found.update({m["mentor_key"]: m["email"] for m in report["unmatched_mentors"]})
    found.update({e["mentee_key"]: e["mentee_email"] for e in report["waitlist"]})
    return found


def test_an_address_travels_with_every_list(ran):
    """The client builds its CSV from these, and reaches a person through any of
    the three, so a list without addresses would export blank cells."""
    _, report = ran
    found = addresses(report)
    assert found
    # Extracted rather than the raw cell: no surrounding text, no padding.
    assert all(
        value == "" or ("@" in value and " " not in value) for value in found.values()
    ), found


def test_an_unreadable_address_exports_as_blank(ran):
    """The row still has to appear, or a real match would vanish from the export."""
    _, report = ran
    flagged = {flag["respondent_key"] for flag in report["review_flags"]}
    found = addresses(report)

    assert flagged, "the sample cohort has somebody with no readable address"
    assert flagged <= set(found), "a flagged person still appears in the report"
    assert all(found[key] == "" for key in flagged)


def test_opening_a_match_shows_both_sets_of_answers(ran):
    client, report = ran
    match = report["matches"][0]
    body = client.post(
        "/api/match",
        json={"mentor_key": match["mentor_key"], "mentee_key": match["mentee_key"]},
    ).json()

    assert body["mentor"]["name"] == match["mentor_name"]
    assert body["percentage"] == match["percentage"]
    assert body["questions"], "answers are what a coordinator opens a match to read"
    assert any(row["mentor_answer"] and row["mentee_answer"] for row in body["questions"])


def test_opening_a_person_shows_their_own_answers(ran):
    client, report = ran
    body = client.post(
        "/api/person", json={"key": report["matches"][0]["mentee_key"]}
    ).json()

    assert body["side"] == "mentee"
    assert body["name"] == report["matches"][0]["mentee_name"]
    # Only answered questions come back, so every row has something to read.
    assert body["questions"]
    assert all(row["answer"] for row in body["questions"])


def test_opening_an_unknown_person_is_a_404(ran):
    client, _ = ran
    response = client.post("/api/person", json={"key": "nobody@example.com"})
    assert response.status_code == 404


# --- recovering after a reload -----------------------------------------------


def test_current_returns_the_report_without_a_fresh_upload(client):
    """What a page reload calls -- solves and reports from an already-scored session
    directly, so this is unaffected by whether the CSV fixtures happen to link against
    the current questions database.
    """
    mentors = [participant("mentor-a", MENTOR), participant("mentor-b", MENTOR)]
    mentees = [participant("mentee-a", MENTEE), participant("mentee-b", MENTEE)]
    scores = score_table({
        ("mentor-a", "mentee-a"): 0.9,
        ("mentor-a", "mentee-b"): 0.1,
        ("mentor-b", "mentee-a"): 0.2,
        ("mentor-b", "mentee-b"): 0.8,
    })

    client.get("/api/current")  # mints this client's session cookie (409s, no upload yet)
    token = client.cookies[main.SESSION_COOKIE]
    main._sessions[token].update(questions=[], mentors=mentors, mentees=mentees, scores=scores)

    body = client.get("/api/current").json()

    matched = {(m["mentor_key"], m["mentee_key"]) for m in body["matches"]}
    assert matched == {("mentor-a", "mentee-a"), ("mentor-b", "mentee-b")}


def test_current_with_nothing_loaded_is_a_409(client):
    assert client.get("/api/current").status_code == 409


# --- session isolation -------------------------------------------------------


def test_two_visitors_are_fully_isolated(synthetic_exports):
    """Two independent cookie jars must never see or affect each other's cohort --
    the whole point of moving off one process-wide session.
    """
    with TestClient(app) as alice, TestClient(app) as bob:
        alice.post("/api/upload", files=uploads(SYNTHETIC_MENTOR, SYNTHETIC_MENTEE))
        alice_report = alice.post("/api/run").json()

        # Bob has uploaded nothing at all, on a completely separate cookie jar.
        assert bob.get("/api/current").status_code == 409

        # Alice's session is untouched by Bob's requests.
        assert alice.get("/api/current").json()["matches"] == alice_report["matches"]

        # Different tokens were actually minted -- this is what the isolation rests on.
        assert alice.cookies[main.SESSION_COOKIE] != bob.cookies[main.SESSION_COOKIE]


# --- disqualification -------------------------------------------------------
#
# Uses cohort A rather than `ran`'s real cohort, which has nobody disqualified.
#
# There is no report field naming who was disqualified, so these tests work
# out the ground truth independently, straight from the source files, the
# same way `synthetic_run` in test_matching.py does for the avoid constraint.


def commitment_disqualified_keys() -> set[str]:
    questions = load_questions(QUESTIONS_DATABASE)
    mentor_frame, mentee_frame = read_export(SYNTHETIC_MENTOR), read_export(SYNTHETIC_MENTEE)
    links = link_columns(questions, mentor_frame, mentee_frame)
    mentors, mentees, _ = prepare(questions, links, mentor_frame, mentee_frame)
    question = find_question(questions, COMMITMENT_QUESTION_PREFIX)
    return disqualified_by_commitment(question, mentors + mentees)


def test_a_run_disqualifies_the_people_who_said_no(synthetic_ran):
    """The wiring in /api/run, not just the matching functions in isolation."""
    _, report = synthetic_ran
    flagged = commitment_disqualified_keys()
    assert flagged, "cohort A must exercise this path"

    matched_keys = {m["mentor_key"] for m in report["matches"]} | {
        m["mentee_key"] for m in report["matches"]
    }
    assert not (flagged & matched_keys), "a disqualified person is never in the auto-matched list"


def test_a_disqualified_person_still_opens_for_manual_review(synthetic_ran):
    """Manual review needs their answers and a real score, same as anyone else."""
    client, report = synthetic_ran
    flagged = commitment_disqualified_keys()
    flagged_mentee = next(
        (m["mentee_key"] for m in report["waitlist"] if m["mentee_key"] in flagged), None
    )
    assert flagged_mentee, "cohort A must include a disqualified mentee"

    person = client.post("/api/person", json={"key": flagged_mentee}).json()
    assert person["questions"], "their own answers are still readable"

    some_mentor = report["matches"][0]["mentor_key"]
    match = client.post(
        "/api/match", json={"mentor_key": some_mentor, "mentee_key": flagged_mentee}
    ).json()
    assert match["percentage"] is not None, "a hand-made pair can still show a real score"


def test_an_address_never_travels_in_a_url():
    """A key is an email address, and the host logs every request path.

    Reading a match or a person is a POST for exactly this reason, so a route
    that takes a key in its path would put the cohort's addresses into logging
    as a side effect of ordinary use.
    """
    paths = [route.path for route in app.routes]
    assert not [
        path for path in paths if "{" in path and path.startswith("/api/")
    ], paths


def test_clearing_drops_the_cohort(real_exports, client):
    """The page's Clear button reaches this, so a finished cohort stops being
    held in memory rather than waiting for the process to stop."""
    client.post("/api/upload", files=uploads(REAL_MENTOR, REAL_MENTEE))
    token = client.cookies[main.SESSION_COOKIE]
    assert main._sessions[token], "the upload loaded something to clear"

    assert client.post("/api/clear").status_code == 200

    assert token not in main._sessions, "clearing drops the session entirely, not just empties it"
    # And the endpoints behind it say so, rather than serving a stale cohort.
    assert client.post("/api/run").status_code == 409


# --- surviving a restart ---------------------------------------------------
#
# These exercise _require's rehydration path and /api/clear's delete directly,
# with session_store's save/load/delete swapped for fakes -- not a full upload
# and run, since that would need a real cohort to reach the two save_session
# call sites, and the wiring being verified here is independent of that: it is
# what happens once something has already been persisted, however it got there.


def test_a_restart_recovers_the_session_from_persistence(monkeypatch, client):
    """What a fresh process looks like right after Cloud Run recycles it: this
    token has no entry yet, but a persisted copy exists and should be recovered
    rather than 409ing.
    """
    monkeypatch.setattr(main, "load_session", lambda token: {"questions": ["recovered"]})

    session: dict = {}
    assert main._require("some-token", session, "questions") == ["recovered"]
    assert session["questions"] == ["recovered"], "recovery has to repopulate the session, not just answer once"


def test_a_restart_with_nothing_persisted_still_409s(monkeypatch, client):
    """No prior upload, no persisted copy -- the ordinary case, unchanged."""
    monkeypatch.setattr(main, "load_session", lambda token: None)

    with pytest.raises(HTTPException) as excinfo:
        main._require("some-token", {}, "questions")
    assert excinfo.value.status_code == 409


def test_clearing_also_deletes_the_persisted_copy(monkeypatch, client):
    """Without this, a coordinator who explicitly clears would find the cohort
    silently back after the next restart."""
    deleted = []
    monkeypatch.setattr(main, "delete_session", lambda token: deleted.append(token))

    assert client.post("/api/clear").status_code == 200
    assert deleted


def test_an_oversized_upload_is_refused(client):
    """Read into memory whole, so without a ceiling one file ends the process."""
    huge = b"Timestamp,Name\n" + b"a,b\n" * (main.MAX_UPLOAD_BYTES // 4)
    response = client.post(
        "/api/upload",
        files={
            "mentor_file": ("mentor.csv", huge),
            "mentee_file": ("mentee.csv", b"Timestamp\n"),
        },
    )

    assert response.status_code == 413
    assert "too large" in response.json()["detail"]
