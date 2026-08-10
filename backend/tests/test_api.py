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
    # Six mentors for four mentees, so everybody is capped at one and the four
    # go to four different mentors, leaving two with nobody.
    assert len(report["unmatched_mentors"]) == 2
    # A missing email is now the only thing raised for review.
    assert report["review_flags"]
    assert all("no email address" in flag["reason"] for flag in report["review_flags"])


def test_opening_a_match_shows_both_sets_of_answers(ran):
    client, report = ran
    match = report["matches"][0]
    body = client.get(f"/api/match/{match['mentor_key']}/{match['mentee_key']}").json()

    assert body["mentor"]["name"] == match["mentor_name"]
    assert body["percentage"] == match["percentage"]
    assert body["questions"], "answers are what a coordinator opens a match to read"
    assert any(row["mentor_answer"] and row["mentee_answer"] for row in body["questions"])
    assert body["scored_questions"] == match["scored_questions"]


def test_capacity_travels_with_every_mentor(ran):
    """The manual area enforces capacity, and picks mentors up from both lists."""
    _, report = ran
    assert all(match["mentor_capacity"] >= 1 for match in report["matches"])
    assert all(mentor["capacity"] >= 1 for mentor in report["unmatched_mentors"])


def test_opening_a_person_shows_their_own_answers(ran):
    client, report = ran
    mentee_key = report["matches"][0]["mentee_key"]
    body = client.get(f"/api/person/{mentee_key}").json()

    assert body["side"] == "mentee"
    assert body["name"] == report["matches"][0]["mentee_name"]
    # Only answered questions come back, so every row has something to read.
    assert body["questions"]
    assert all(row["answer"] for row in body["questions"])


def test_opening_an_unknown_person_is_a_404(ran):
    client, _ = ran
    assert client.get("/api/person/nobody@example.com").status_code == 404


