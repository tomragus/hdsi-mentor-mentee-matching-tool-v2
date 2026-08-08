"""Tests for the HTTP surface."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
REAL_MENTOR = FIXTURES / "mentor_responses.csv"
REAL_MENTEE = FIXTURES / "mentee_responses.csv"


@pytest.fixture
def client():
    main._session.clear()
    with TestClient(app) as test_client:
        yield test_client


def uploads(mentor_path: Path, mentee_path: Path):
    return {
        "mentor_file": ("mentor.csv", mentor_path.read_bytes()),
        "mentee_file": ("mentee.csv", mentee_path.read_bytes()),
    }


@pytest.fixture(scope="module")
def ran(real_exports):
    """One real run, shared, since scoring loads the embedding model."""
    main._session.clear()
    with TestClient(app) as test_client:
        test_client.post("/api/upload", files=uploads(REAL_MENTOR, REAL_MENTEE))
        report = test_client.post("/api/run").json()
        yield test_client, report


def test_upload_names_the_questions_it_could_not_find(real_exports, client):
    """The whole point of the Step 4 error is saying which question is missing."""
    broken = (FIXTURES / "mentor_responses.csv").read_text().splitlines()
    broken[0] = broken[0].replace("Graduation Year", "Grad Year")
    response = client.post(
        "/api/upload",
        files={
            "mentor_file": ("mentor.csv", "\n".join(broken).encode()),
            "mentee_file": ("mentee.csv", (FIXTURES / "mentee_responses.csv").read_bytes()),
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert any("Graduation Year" in item["question"] for item in detail["missing"])


def test_run_returns_the_whole_report(ran):
    _, report = ran
    assert len(report["matches"]) == 4
    assert report["waitlist"] == []
    assert report["unfilled_slots"] == 5
    assert len(report["cutoffs"]) == 8
    assert report["review_flags"], "the missing email and unreadable location"


def test_blocked_pairings_are_left_out_of_the_matches(ran):
    _, report = ran
    blocked = {(b["mentor_key"], b["mentee_key"]) for b in report["avoid_blocks"]}
    assigned = {(m["mentor_key"], m["mentee_key"]) for m in report["matches"]}
    assert not (blocked & assigned)


def test_opening_a_match_shows_both_sets_of_answers(ran):
    client, report = ran
    match = report["matches"][0]
    body = client.get(f"/api/match/{match['mentor_key']}/{match['mentee_key']}").json()

    assert body["mentor"]["name"] == match["mentor_name"]
    assert body["percentage"] == match["percentage"]
    assert body["questions"], "answers are what a coordinator opens a match to read"
    assert any(row["mentor_answer"] and row["mentee_answer"] for row in body["questions"])
    assert any(row["points"] is not None for row in body["questions"])


