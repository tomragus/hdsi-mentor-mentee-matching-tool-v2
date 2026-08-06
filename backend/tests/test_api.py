"""Tests for the HTTP surface."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"


@pytest.fixture
def client():
    api._session.clear()
    with TestClient(app) as test_client:
        yield test_client


def uploads(directory: Path):
    return {
        "mentor_file": ("mentor.csv", (directory / "mentor_responses.csv").read_bytes()),
        "mentee_file": ("mentee.csv", (directory / "mentee_responses.csv").read_bytes()),
    }


@pytest.fixture
def uploaded(real_exports, client):
    response = client.post("/api/upload", files=uploads(FIXTURES))
    assert response.status_code == 200
    return client


@pytest.fixture(scope="module")
def ran(real_exports):
    """One real run, shared, since scoring loads the embedding model."""
    api._session.clear()
    with TestClient(app) as test_client:
        test_client.post("/api/upload", files=uploads(FIXTURES))
        report = test_client.post("/api/run").json()
        yield test_client, report


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_upload_reports_what_it_read(uploaded):
    response = uploaded.post("/api/upload", files=uploads(FIXTURES))
    body = response.json()
    assert body == {"mentor_rows": 6, "mentee_rows": 4, "questions": 24}


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


def test_endpoints_refuse_to_work_before_an_upload(client):
    assert client.post("/api/run").status_code == 409
    assert client.get("/api/match/a/b").status_code == 409


def test_run_returns_the_whole_report(ran):
    _, report = ran
    assert len(report["matches"]) == 4
    assert report["waitlist"] == []
    assert report["unfilled_slots"] == 5
    assert len(report["cutoffs"]) == 8
    assert report["review_flags"], "the missing email and unreadable location"


def test_matches_are_ranked(ran):
    _, report = ran
    percentages = [match["percentage"] for match in report["matches"]]
    assert percentages == sorted(percentages, reverse=True)


def test_avoid_blocks_carry_their_triggering_terms(ran):
    _, report = ran
    assert len(report["avoid_blocks"]) == 1
    block = report["avoid_blocks"][0]
    assert block["mentor_triggers"] == ["finance"]
    assert block["mentor_name"] and block["mentee_name"]


def test_blocked_pairings_are_left_out_of_the_matches(ran):
    _, report = ran
    blocked = {(b["mentor_key"], b["mentee_key"]) for b in report["avoid_blocks"]}
    assigned = {(m["mentor_key"], m["mentee_key"]) for m in report["matches"]}
    assert not (blocked & assigned)


def test_adjustment_endpoints_are_not_exposed(ran):
    """Pin, forbid, and override are deliberately not wired up."""
    client, _ = ran
    body = {"mentor_key": "a", "mentee_key": "b"}
    for path in ("/api/pin", "/api/forbid", "/api/override", "/api/reset"):
        assert client.post(path, json=body).status_code == 404


def test_cutoffs_name_their_question(ran):
    _, report = ran
    assert all(cutoff["question"] for cutoff in report["cutoffs"])
    assert all(cutoff["pair_count"] > 0 for cutoff in report["cutoffs"])


def test_opening_a_match_shows_both_sets_of_answers(ran):
    client, report = ran
    match = report["matches"][0]
    body = client.get(f"/api/match/{match['mentor_key']}/{match['mentee_key']}").json()

    assert body["mentor"]["name"] == match["mentor_name"]
    assert body["percentage"] == match["percentage"]
    assert body["questions"], "answers are what a coordinator opens a match to read"
    assert any(row["mentor_answer"] and row["mentee_answer"] for row in body["questions"])
    assert any(row["points"] is not None for row in body["questions"])


def test_opening_an_unknown_match_is_a_404(ran):
    client, _ = ran
    assert client.get("/api/match/nobody/nobody").status_code == 404
