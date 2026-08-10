"""What a coordinator sees after a solve.

Assembly, not judgment. The response shape is built here directly rather than
being modelled as dataclasses and then copied field by field into a dict --
there is one caller and one consumer, and the duplication bought nothing.

Everything here is display. The assignment itself is already decided by the
time any of this runs.
"""

from app.inputs import missing_email
from app.matching import PairScore, Participant

NO_EMAIL_REASON = (
    "no email address given, so duplicate submissions cannot be detected "
    "for this respondent"
)


def _flags(mentors: list[Participant], mentees: list[Participant]) -> list[dict]:
    """The one thing raised for review: a respondent with no readable address."""
    return [
        {"respondent_key": person.respondent.key, "reason": NO_EMAIL_REASON}
        for person in mentors + mentees
        if missing_email(person.respondent)
    ]


def build_report(
    mentors: list[Participant],
    mentees: list[Participant],
    solution,
) -> dict:
    """Assemble the response for one solve."""
    names = {
        person.respondent.key: person.respondent.name
        for person in mentors + mentees
    }
    capacities = {
        mentor.respondent.key: mentor.respondent.capacity for mentor in mentors
    }
    waiting = set(solution.unassigned)
    took = {assignment.mentor_key for assignment in solution.assignments}

    return {
        "matches": [
            {
                "mentor_key": assignment.mentor_key,
                "mentor_name": names[assignment.mentor_key],
                "mentee_key": assignment.mentee_key,
                "mentee_name": names[assignment.mentee_key],
                "percentage": round(assignment.score.percentage, 1),
                "scored_questions": assignment.score.scored_questions,
                "mentor_capacity": capacities[assignment.mentor_key],
            }
            for assignment in solution.assignments
        ],
        "waitlist": [
            {
                "mentee_key": mentee.respondent.key,
                "mentee_name": mentee.respondent.name,
            }
            for mentee in mentees
            if mentee.respondent.key in waiting
        ],
        # A mentor who offered two places and filled one is not here: they took
        # somebody. This is only the mentors who took nobody at all.
        "unmatched_mentors": [
            {
                "mentor_key": mentor.respondent.key,
                "mentor_name": mentor.respondent.name,
                "capacity": mentor.respondent.capacity,
            }
            for mentor in mentors
            if mentor.respondent.key not in took
        ],
        "review_flags": _flags(mentors, mentees),
    }


def match_detail(
    mentor: Participant,
    mentee: Participant,
    score: PairScore | None,
    questions: list,
) -> dict:
    """Both people's answers side by side, for checking a pairing by hand.

    Works for any pairing, not only assigned ones, since every pair is scored.
    """
    rows = []
    for question in questions:
        mentor_answer = mentor.respondent.responses.get(question.row, "")
        mentee_answer = mentee.respondent.responses.get(question.row, "")
        if not mentor_answer and not mentee_answer:
            continue
        rows.append(
            {
                "row": question.row,
                "question": question.mentor_question,
                "mentor_answer": mentor_answer,
                "mentee_answer": mentee_answer,
            }
        )

    return {
        "mentor": {"key": mentor.respondent.key, "name": mentor.respondent.name},
        "mentee": {"key": mentee.respondent.key, "name": mentee.respondent.name},
        "percentage": round(score.percentage, 1) if score else None,
        # How many questions the score rests on. Derived here rather than left
        # for the frontend to count.
        "scored_questions": score.scored_questions if score else 0,
        "questions": rows,
    }
