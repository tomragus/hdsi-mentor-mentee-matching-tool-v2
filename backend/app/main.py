"""HTTP surface for the matching tool.

Deliberately small: upload the two exports, run the match, and open one match or
one person to read the answers behind it.

State lives in this module for the length of the process. That is a real
limitation -- restarting the server loses an uploaded cohort -- and it is the
right trade for a tool one coordinator runs a few times a cycle. Manual
adjustments live in the frontend, layered over the report rather than fed back
into the solver, so nothing here knows about them.

Response shapes are built here directly rather than modelled as dataclasses and
copied field by field into a dict: there is one caller and one consumer.
"""

import io
import logging
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import NAME_QUESTION, QUESTIONS_DATABASE, normalize
from app.inputs import (
    MENTOR, READ_MALFORMED, READ_WRONG_TYPE, ROLE_AVOID, ExportLinkError, ExportReadError,
    Question, Respondent, email_address, for_display, link_columns, load_questions,
    missing_email, read_export,
)
from app.matching import (
    PairScore, Participant, blocked_cells, build_vocabulary, extract_avoid_terms, prepare,
    score_all, solve, stated_terms_for_all,
)

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

_session: dict = {}  # the single in-memory session, empty until an upload succeeds

NO_EMAIL_REASON = "no email address given"

# A form export is a few hundred rows of text. This is far larger than one, and
# exists because the upload is read into memory whole: without a ceiling a single
# large file exhausts the process, which on a one-instance deployment also
# discards the cohort somebody was working on.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# What to tell somebody whose upload could not be read. Two files are uploaded
# together, so each message names the one at fault.
READ_ADVICE = {
    READ_WRONG_TYPE: 'Incorrect file type. "{name}" is not a readable .csv or .xlsx file.',
    READ_MALFORMED: (
        "Double check your sheets for formatting issues and junk data. "
        '"{name}" could not be read as a table.'
    ),
}


def _require(key: str):
    value = _session.get(key)
    if value is None:
        raise HTTPException(status_code=409, detail="Upload both exports first.")
    return value


def name_row(questions: list[Question]) -> int | None:
    """Which database row asks for the respondent's name."""
    wanted = normalize(NAME_QUESTION)
    return next((q.row for q in questions if normalize(q.mentor_question) == wanted), None)


def displayed_answer(respondent: Respondent, row: int, names: int | None) -> str:
    """One answer as it should be read. Every row shows what was typed, except a blank name
    row: that falls back to whatever identifies the person, since they are named on the
    leaderboard and an empty cell here would only raise the question of whose row it is.
    """
    answer = respondent.responses.get(row, "")
    return respondent.name if not answer and row == names else answer


def build_report(mentors: list[Participant], mentees: list[Participant], solution) -> dict:
    """Assemble the response for one solve."""
    names = {p.respondent.key: p.respondent.name for p in mentors + mentees}
    capacities = {m.respondent.key: m.respondent.capacity for m in mentors}
    # Every list carries addresses, because the client builds its CSV export from
    # them and a person can reach that export through any of the three.
    emails = {p.respondent.key: email_address(p.respondent) for p in mentors + mentees}
    waiting = set(solution.unassigned)
    took = {assignment.mentor_key for assignment in solution.assignments}

    return {
        "matches": [
            {
                "mentor_key": a.mentor_key,
                "mentor_name": names[a.mentor_key],
                "mentor_email": emails[a.mentor_key],
                "mentee_key": a.mentee_key,
                "mentee_name": names[a.mentee_key],
                "mentee_email": emails[a.mentee_key],
                "percentage": round(a.score.percentage, 1),
                "mentor_capacity": capacities[a.mentor_key],
            }
            for a in solution.assignments
        ],
        "waitlist": [
            {
                "mentee_key": m.respondent.key,
                "mentee_name": m.respondent.name,
                "mentee_email": emails[m.respondent.key],
            }
            for m in mentees
            if m.respondent.key in waiting
        ],
        # A mentor who offered two places and filled one is not here: they took
        # somebody. This is only the mentors who took nobody at all.
        "unmatched_mentors": [
            {
                "mentor_key": m.respondent.key,
                "mentor_name": m.respondent.name,
                "email": emails[m.respondent.key],
                "capacity": m.respondent.capacity,
            }
            for m in mentors
            if m.respondent.key not in took
        ],
        # The one thing raised for review: a respondent with no readable address.
        "review_flags": [
            {"respondent_key": p.respondent.key, "reason": NO_EMAIL_REASON}
            for p in mentors + mentees
            if missing_email(p.respondent)
        ],
    }


def match_detail(
    mentor: Participant, mentee: Participant, score: PairScore | None, questions: list[Question]
) -> dict:
    """Both people's answers side by side, for checking a pairing by hand. Works for any
    pairing, not only assigned ones, since every pair is scored.
    """
    names = name_row(questions)
    rows = []
    for question in questions:
        mentor_answer = displayed_answer(mentor.respondent, question.row, names)
        mentee_answer = displayed_answer(mentee.respondent, question.row, names)
        if mentor_answer or mentee_answer:
            rows.append({
                "row": question.row,
                "question": question.mentor_question,
                "mentor_answer": mentor_answer,
                "mentee_answer": mentee_answer,
            })

    return {
        "mentor": {"key": mentor.respondent.key, "name": mentor.respondent.name},
        "mentee": {"key": mentee.respondent.key, "name": mentee.respondent.name},
        "percentage": round(score.percentage, 1) if score else None,
        "questions": rows,
    }


# --- endpoints ------------------------------------------------------------


@app.post("/api/upload")
async def upload(mentor_file: UploadFile, mentee_file: UploadFile) -> dict:
    """Accept both exports, checking every question resolves to a column."""
    for upload_file in (mentor_file, mentee_file):
        if upload_file.size is not None and upload_file.size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f'"{Path(upload_file.filename or "file").name}" is too large. '
                    f"Uploads are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                ),
            )

    questions = load_questions(QUESTIONS_DATABASE)

    try:
        mentor_frame, mentee_frame = (
            read_export(io.BytesIO(f.file.read()), name=f.filename)
            for f in (mentor_file, mentee_file)
        )
    except ExportReadError as error:
        # Unreadable is a problem with the upload, not with this server, so it
        # answers 400 with advice rather than failing and looking like an outage.
        raise HTTPException(
            status_code=400, detail=READ_ADVICE[error.kind].format(name=error.filename)
        ) from error

    swapped = False
    try:
        links = link_columns(questions, mentor_frame, mentee_frame)
    except ExportLinkError as error:
        # The two forms word most questions differently, so a pair that will not
        # link this way round and links cleanly the other way round was uploaded
        # into the wrong two boxes. That is the likeliest mistake there is, and
        # swapping them is the whole of the fix.
        try:
            links = link_columns(questions, mentee_frame, mentor_frame)
        except ExportLinkError:
            # Genuinely mismatched. Naming the unresolved questions is the whole
            # point of the error, so it is passed through rather than flattened.
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
        mentor_frame, mentee_frame = mentee_frame, mentor_frame
        swapped = True
        logger.info("uploads were the wrong way round; read them swapped")

    _session.clear()
    _session.update(
        questions=questions, links=links, mentor_frame=mentor_frame, mentee_frame=mentee_frame
    )
    # Only the fact that they were read the other way round; the wording of it
    # belongs to the client.
    return {"swapped": swapped}


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


@app.post("/api/match")
def match(mentor_key: str = Body(...), mentee_key: str = Body(...)) -> dict:
    """Both people's answers, side by side, for checking a match by hand.

    Reads rather than changes anything, so this would ordinarily be a GET. A
    person's key is their email address, and the host logs every request path,
    so a key in the URL would file the cohort's addresses into logging simply
    because somebody clicked through their matches. Request bodies are not
    logged, which is the whole reason for the method.
    """
    mentors = _require("mentors")
    mentor = next((m for m in mentors if m.respondent.key == mentor_key), None)
    mentee = next((m for m in _session["mentees"] if m.respondent.key == mentee_key), None)
    if mentor is None or mentee is None:
        raise HTTPException(status_code=404, detail="No such mentor or mentee.")

    return match_detail(
        mentor,
        mentee,
        _session["scores"].get((mentor_key, mentee_key)),
        for_display(_session["questions"]),
    )


@app.post("/api/person")
def person(key: str = Body(..., embed=True)) -> dict:
    """One person's own answers, for reading a card in the manual area.

    A POST for the same reason /api/match is one: the key is an email address.
    """
    everyone = _require("mentors") + _session["mentees"]
    found = next((p for p in everyone if p.respondent.key == key), None)
    if found is None:
        raise HTTPException(status_code=404, detail="No such mentor or mentee.")

    respondent = found.respondent
    questions = for_display(_session["questions"])
    names = name_row(questions)
    return {
        "key": key,
        "name": respondent.name,
        "side": respondent.side,
        "email": respondent.email,
        "capacity": respondent.capacity,
        "questions": [
            {
                "row": q.row,
                "question": q.mentor_question if respondent.side == MENTOR else q.mentee_question,
                "answer": displayed_answer(respondent, q.row, names),
            }
            for q in questions
            # A question this side was never asked, or simply left blank, has
            # nothing to show.
            if displayed_answer(respondent, q.row, names)
        ],
    }


@app.post("/api/clear")
def clear() -> dict[str, str]:
    """Drop the uploaded cohort.

    The page's Clear button reaches this. Nothing in the client can use a cohort
    once the report is gone -- pressing Match always uploads again -- so a copy
    kept here after that point is only names, addresses and written answers
    sitting in memory for however long the process happens to live, which on a
    deployment that sleeps when idle is not a length anyone can predict.
    """
    _session.clear()
    return {"status": "cleared"}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --- the built frontend ---------------------------------------------------

# Deployed, one process serves both the API and the page, so there is a single
# URL and no CORS. In development this directory may be missing or stale and Vite
# serves the UI on its own port instead, which is why the mount is conditional.
# It goes last, after every route above, so /api keeps winning.
FRONTEND = Path(__file__).parents[2] / "frontend" / "dist"

if FRONTEND.is_dir():
    # html=True falls back to index.html, which is what makes a single-page app
    # survive a refresh on any path.
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
else:
    logger.info("no built frontend at %s; serving the API only", FRONTEND)
