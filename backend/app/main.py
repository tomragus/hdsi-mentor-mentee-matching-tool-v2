"""HTTP surface for the matching tool.

Deliberately small: upload the two exports, run the match, and open one match
to read the answers behind it.

State lives in this module for the length of the process. That is a real
limitation -- restarting the server loses an uploaded cohort -- and it is the
right trade for a tool one coordinator runs a few times a cycle.

Manual adjustments live in the frontend, layered over this report rather than
fed back into the solver, so nothing here knows about them.
"""

import io
import logging

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.avoid import (
    blocked_cells,
    build_vocabulary,
    extract_avoid_terms,
    stated_terms_for_all,
)
from app.config import QUESTIONS_DATABASE
from app.inputs import MENTOR, ExportLinkError, link_columns, read_export
from app.matching import prepare, score_all, solve
from app.questions import ROLE_AVOID, for_display, load_questions
from app.report import build_report, match_detail

logger = logging.getLogger(__name__)

app = FastAPI(title="HDSI Mentor/Mentee Matching Tool")

# The Vite dev server runs on a different port, so the browser treats API calls
# as cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# The single in-memory session. Empty until an upload succeeds.
_session: dict = {}


def _read_upload(upload: UploadFile):
    return read_export(io.BytesIO(upload.file.read()), name=upload.filename)


def _require(key: str):
    value = _session.get(key)
    if value is None:
        raise HTTPException(status_code=409, detail="Upload both exports first.")
    return value


@app.post("/api/upload")
async def upload(mentor_file: UploadFile, mentee_file: UploadFile) -> dict:
    """Accept both exports, checking every question resolves to a column."""
    questions = load_questions(QUESTIONS_DATABASE)
    mentor_frame = _read_upload(mentor_file)
    mentee_frame = _read_upload(mentee_file)

    try:
        links = link_columns(questions, mentor_frame, mentee_frame)
    except ExportLinkError as error:
        # Naming the unresolved questions is the whole point of the error, so
        # it is passed through rather than flattened to "bad request".
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Some questions could not be found in the uploads.",
                "missing": [
                    {"side": side, "row": row, "question": text}
                    for side, row, text in error.missing
                ],
            },
        ) from error

    _session.clear()
    _session.update(
        questions=questions,
        links=links,
        mentor_frame=mentor_frame,
        mentee_frame=mentee_frame,
    )
    return {
        "mentor_rows": len(mentor_frame),
        "mentee_rows": len(mentee_frame),
        "questions": len(questions),
    }


@app.post("/api/run")
def run() -> dict:
    """Score the whole cohort and solve it. The slow call."""
    questions = _require("questions")
    mentors, mentees, context = prepare(
        questions, _session["links"], _session["mentor_frame"], _session["mentee_frame"]
    )
    scores = score_all(context, mentors, mentees)

    people = [p.respondent for p in mentors + mentees]
    avoid_question = next((q for q in questions if q.role == ROLE_AVOID), None)
    excluded: set[tuple[str, str]] = set()
    if avoid_question is not None:
        vocabulary = build_vocabulary(questions, people)
        excluded = blocked_cells(
            [p.respondent for p in mentors],
            [p.respondent for p in mentees],
            extract_avoid_terms(avoid_question, people, vocabulary),
            stated_terms_for_all(questions, people, vocabulary),
        )

    solution = solve(mentors, mentees, scores, blocked=excluded)
    _session.update(mentors=mentors, mentees=mentees, scores=scores)
    return build_report(mentors, mentees, solution)


@app.get("/api/match/{mentor_key}/{mentee_key}")
def match(mentor_key: str, mentee_key: str) -> dict:
    """Both people's answers, side by side, for checking a match by hand."""
    mentors = _require("mentors")
    mentor = next((m for m in mentors if m.respondent.key == mentor_key), None)
    mentee = next(
        (m for m in _session["mentees"] if m.respondent.key == mentee_key), None
    )
    if mentor is None or mentee is None:
        raise HTTPException(status_code=404, detail="No such mentor or mentee.")

    return match_detail(
        mentor,
        mentee,
        _session["scores"].get((mentor_key, mentee_key)),
        for_display(_session["questions"]),
    )


@app.get("/api/person/{key}")
def person(key: str) -> dict:
    """One person's own answers, for reading a card in the manual area."""
    everyone = _require("mentors") + _session["mentees"]
    found = next((p for p in everyone if p.respondent.key == key), None)
    if found is None:
        raise HTTPException(status_code=404, detail="No such mentor or mentee.")

    respondent = found.respondent
    return {
        "key": key,
        "name": respondent.name,
        "side": respondent.side,
        "email": respondent.email,
        "capacity": respondent.capacity,
        "questions": [
            {
                "row": question.row,
                "question": (
                    question.mentor_question
                    if respondent.side == MENTOR
                    else question.mentee_question
                ),
                "answer": respondent.responses[question.row],
            }
            for question in for_display(_session["questions"])
            # A question this side was never asked, or simply left blank, has
            # nothing to show.
            if respondent.responses.get(question.row)
        ],
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
