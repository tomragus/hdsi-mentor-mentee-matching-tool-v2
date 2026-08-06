"""HTTP surface for the matching tool.

Deliberately small: upload the two exports, run the match, and open one match
to read the answers behind it.

State lives in this module for the length of the process. That is a real
limitation -- restarting the server loses an uploaded cohort -- and it is the
right trade for a tool one coordinator runs a few times a cycle.

Manual adjustments (pinning a pairing, forbidding one, overriding an avoid
block) are not exposed. The machinery for all three is still in `assignment.py`
and `avoid.py` with its tests, so it can be wired back up without rework.
"""

import io
import logging

from fastapi import APIRouter, HTTPException, UploadFile

from app.assignment import solve
from app.avoid import (
    blocked_cells,
    blocked_pairs,
    build_vocabulary,
    extract_avoid_terms,
    stated_terms_for_all,
)
from app.config import QUESTIONS_DATABASE
from app.exports import ExportLinkError, link_columns, read_export
from app.pairs import prepare, score_all
from app.questions import ROLE_AVOID, load_questions
from app.report import build_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# The single in-memory session. Empty until an upload succeeds.
_session: dict = {}


def _read_upload(upload: UploadFile):
    return read_export(io.BytesIO(upload.file.read()), name=upload.filename)


def _require(key: str):
    value = _session.get(key)
    if value is None:
        raise HTTPException(status_code=409, detail="Upload both exports first.")
    return value


def _serialize_report(report, names: dict[str, str]) -> dict:
    return {
        "matches": [
            {
                "mentor_key": match.mentor_key,
                "mentor_name": match.mentor_name,
                "mentee_key": match.mentee_key,
                "mentee_name": match.mentee_name,
                "percentage": round(match.percentage, 1),
                "scored_questions": match.scored_questions,
            }
            for match in report.matches
        ],
        "waitlist": [
            {
                "mentee_key": entry.mentee_key,
                "mentee_name": entry.mentee_name,
                "best_percentage": (
                    None if entry.best_percentage is None
                    else round(entry.best_percentage, 1)
                ),
                "best_mentor_name": names.get(entry.best_mentor_key or ""),
            }
            for entry in report.waitlist
        ],
        "blocking_pairs": [
            {
                "mentor_key": pair.mentor_key,
                "mentor_name": pair.mentor_name,
                "mentee_key": pair.mentee_key,
                "mentee_name": pair.mentee_name,
                "percentage": round(pair.percentage, 1),
                "mentor_current_percentage": (
                    None if pair.mentor_current_percentage is None
                    else round(pair.mentor_current_percentage, 1)
                ),
                "mentee_current_percentage": (
                    None if pair.mentee_current_percentage is None
                    else round(pair.mentee_current_percentage, 1)
                ),
            }
            for pair in report.blocking_pairs
        ],
        "avoid_blocks": [
            {
                "mentor_key": block.mentor_key,
                "mentor_name": names.get(block.mentor_key, block.mentor_key),
                "mentee_key": block.mentee_key,
                "mentee_name": names.get(block.mentee_key, block.mentee_key),
                "mentor_triggers": list(block.mentor_triggers),
                "mentee_triggers": list(block.mentee_triggers),
            }
            for block in report.avoid_blocks
        ],
        "review_flags": [
            {
                "side": flag.side,
                "respondent_key": flag.respondent_key,
                "name": names.get(flag.respondent_key, flag.respondent_key),
                "reason": flag.reason,
            }
            for flag in report.review_flags
        ],
        "cutoffs": [
            {
                "row": cutoff.row,
                "question": _session["question_text"].get(cutoff.row, ""),
                "percentiles": list(cutoff.percentiles),
                "upper": round(cutoff.upper, 3),
                "lower": round(cutoff.lower, 3),
                "pair_count": cutoff.pair_count,
            }
            for cutoff in sorted(report.cutoffs, key=lambda c: c.row)
        ],
        "unfilled_slots": report.unfilled_slots,
    }


@router.post("/upload")
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


@router.post("/run")
def run() -> dict:
    """Score the whole cohort and solve it. The slow call."""
    questions = _require("questions")
    mentors, mentees, context, flags = prepare(
        questions, _session["links"], _session["mentor_frame"], _session["mentee_frame"]
    )
    scores = score_all(context, mentors, mentees)

    people = [p.respondent for p in mentors + mentees]
    avoid_question = next((q for q in questions if q.role == ROLE_AVOID), None)
    blocks, avoid_flags = [], []
    if avoid_question is not None:
        vocabulary = build_vocabulary(questions, people)
        extracted, avoid_flags = extract_avoid_terms(avoid_question, people, vocabulary)
        stated = stated_terms_for_all(questions, people, vocabulary)
        blocks = blocked_pairs(
            [p.respondent for p in mentors],
            [p.respondent for p in mentees],
            extracted,
            stated,
        )

    excluded = blocked_cells(blocks)
    solution = solve(mentors, mentees, scores, blocked=excluded)
    report = build_report(
        mentors,
        mentees,
        scores,
        solution,
        blocks,
        flags + avoid_flags,
        context.cutoffs,
        excluded=excluded,
    )

    _session.update(
        mentors=mentors,
        mentees=mentees,
        scores=scores,
        question_text={q.row: q.mentor_question for q in questions},
    )
    return _serialize_report(
        report, {p.respondent.key: p.respondent.name for p in mentors + mentees}
    )


@router.get("/match/{mentor_key}/{mentee_key}")
def match(mentor_key: str, mentee_key: str) -> dict:
    """Both people's answers, side by side, for checking a match by hand."""
    mentors = _require("mentors")
    mentor = next((m for m in mentors if m.respondent.key == mentor_key), None)
    mentee = next(
        (m for m in _session["mentees"] if m.respondent.key == mentee_key), None
    )
    if mentor is None or mentee is None:
        raise HTTPException(status_code=404, detail="No such mentor or mentee.")

    score = _session["scores"].get((mentor_key, mentee_key))
    by_row = {item.row: item for item in (score.question_scores if score else ())}

    rows = []
    for question in _session["questions"]:
        mentor_answer = mentor.respondent.responses.get(question.row, "")
        mentee_answer = mentee.respondent.responses.get(question.row, "")
        if not mentor_answer and not mentee_answer:
            continue
        detail = by_row.get(question.row)
        rows.append(
            {
                "row": question.row,
                "weight": question.weight,
                "mentor_question": question.mentor_question,
                "mentee_question": question.mentee_question,
                "mentor_answer": mentor_answer,
                "mentee_answer": mentee_answer,
                "points": detail.points if detail else None,
                "contribution": detail.contribution if detail else None,
                "maximum": detail.maximum if detail else None,
                "penalty": detail.penalty if detail else 0,
            }
        )

    return {
        "mentor": {
            "key": mentor_key,
            "name": mentor.respondent.name,
            "email": mentor.respondent.email,
        },
        "mentee": {
            "key": mentee_key,
            "name": mentee.respondent.name,
            "email": mentee.respondent.email,
        },
        "percentage": round(score.percentage, 1) if score else None,
        "raw": score.raw if score else None,
        "maximum": score.maximum if score else None,
        "questions": rows,
    }
