"""What a coordinator sees after a solve.

The assignment maximizes the cohort's total compatibility, which is not the
same as making every individual happy. A mentor and a mentee can each prefer
the other over the partner they were given, and the solve is still optimal
overall. Those pairs are worth surfacing rather than hiding, so they can be
looked at by hand.

Everything else here is assembly: the ranked matches, the waitlist in the order
a coordinator would work through it, and the things earlier steps set aside for
review.
"""

import logging
from dataclasses import dataclass

from app.avoid import BlockedPair
from app.matching import PairScore, Participant
from app.inputs import ReviewFlag
from app.scoring import Cutoffs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchRow:
    """One assigned pairing, as displayed. Plain immutable record."""

    mentor_key: str
    mentor_name: str
    mentee_key: str
    mentee_name: str
    percentage: float
    # How many questions the score rests on, so a thin match is visible.
    scored_questions: int


@dataclass(frozen=True)
class WaitlistEntry:
    """A mentee who was not placed. Plain immutable record."""

    mentee_key: str
    mentee_name: str
    # Their best score among mentors they were not blocked from.
    best_percentage: float | None
    best_mentor_key: str | None


@dataclass(frozen=True)
class BlockingPair:
    """Two people who would both rather have each other. Plain immutable record."""

    mentor_key: str
    mentor_name: str
    mentee_key: str
    mentee_name: str
    percentage: float
    # What each of them got instead. None means nothing at all.
    mentor_current_percentage: float | None
    mentee_current_percentage: float | None


@dataclass(frozen=True)
class Report:
    """Everything one solve produced. Plain immutable record."""

    matches: tuple[MatchRow, ...]
    waitlist: tuple[WaitlistEntry, ...]
    blocking_pairs: tuple[BlockingPair, ...]
    avoid_blocks: tuple[BlockedPair, ...]
    review_flags: tuple[ReviewFlag, ...]
    cutoffs: tuple[Cutoffs, ...]
    unfilled_slots: int


def _names(*groups: list[Participant]) -> dict[str, str]:
    return {
        person.respondent.key: person.respondent.name
        for group in groups
        for person in group
    }


def _mentor_thresholds(
    mentors: list[Participant], assignments
) -> dict[str, float]:
    """The score a mentor would have to beat to want to swap.

    A mentor with a free slot has nothing to give up, so anyone beats it.
    """
    taken: dict[str, list[float]] = {}
    for assignment in assignments:
        taken.setdefault(assignment.mentor_key, []).append(assignment.score.normalized)

    thresholds = {}
    for mentor in mentors:
        key = mentor.respondent.key
        capacity = max(1, mentor.respondent.capacity)
        current = taken.get(key, [])
        thresholds[key] = min(current) if len(current) >= capacity else float("-inf")
    return thresholds


def find_blocking_pairs(
    mentors: list[Participant],
    mentees: list[Participant],
    scores: dict[tuple[str, str], PairScore],
    assignments,
    excluded: set[tuple[str, str]],
) -> list[BlockingPair]:
    """Find pairs where both sides would rather have each other.

    A global optimum can contain these: giving one pair what they want may cost
    the cohort more than it gains. Reporting them lets a coordinator decide.
    """
    names = _names(mentors, mentees)
    thresholds = _mentor_thresholds(mentors, assignments)
    mentee_current = {a.mentee_key: a.score.normalized for a in assignments}
    assigned_pairs = {(a.mentor_key, a.mentee_key) for a in assignments}
    mentor_current = {
        mentor.respondent.key: (
            None if thresholds[mentor.respondent.key] == float("-inf")
            else thresholds[mentor.respondent.key]
        )
        for mentor in mentors
    }

    found = []
    for mentor in mentors:
        mentor_key = mentor.respondent.key
        for mentee in mentees:
            mentee_key = mentee.respondent.key
            pair = (mentor_key, mentee_key)
            if pair in assigned_pairs or pair in excluded:
                continue

            score = scores.get(pair)
            if score is None:
                continue

            # An unassigned mentee has nothing to give up either.
            mentee_threshold = mentee_current.get(mentee_key, float("-inf"))
            if score.normalized <= thresholds[mentor_key]:
                continue
            if score.normalized <= mentee_threshold:
                continue

            found.append(
                BlockingPair(
                    mentor_key=mentor_key,
                    mentor_name=names[mentor_key],
                    mentee_key=mentee_key,
                    mentee_name=names[mentee_key],
                    percentage=score.percentage,
                    mentor_current_percentage=(
                        None if mentor_current[mentor_key] is None
                        else mentor_current[mentor_key] * 100
                    ),
                    mentee_current_percentage=(
                        mentee_current[mentee_key] * 100
                        if mentee_key in mentee_current
                        else None
                    ),
                )
            )

    found.sort(key=lambda item: item.percentage, reverse=True)
    logger.info("found %d blocking pairs", len(found))
    return found


def build_waitlist(
    mentees: list[Participant],
    unassigned: tuple[str, ...],
    scores: dict[tuple[str, str], PairScore],
    excluded: set[tuple[str, str]],
) -> list[WaitlistEntry]:
    """Unplaced mentees, best prospects first."""
    names = _names(mentees)
    waiting = set(unassigned)

    entries = []
    for mentee in mentees:
        key = mentee.respondent.key
        if key not in waiting:
            continue

        available = [
            score
            for (mentor_key, mentee_key), score in scores.items()
            if mentee_key == key and (mentor_key, mentee_key) not in excluded
        ]
        best = max(available, key=lambda s: s.normalized, default=None)
        entries.append(
            WaitlistEntry(
                mentee_key=key,
                mentee_name=names[key],
                best_percentage=best.percentage if best else None,
                best_mentor_key=best.mentor_key if best else None,
            )
        )

    # Best prospects first, so a coordinator who frees up a slot knows who to
    # place next.
    entries.sort(
        key=lambda entry: (entry.best_percentage is not None, entry.best_percentage or 0),
        reverse=True,
    )
    return entries


def build_report(
    mentors: list[Participant],
    mentees: list[Participant],
    scores: dict[tuple[str, str], PairScore],
    solution,
    avoid_blocks: list[BlockedPair],
    review_flags: list[ReviewFlag],
    cutoffs: dict[int, Cutoffs],
    excluded: set[tuple[str, str]] | None = None,
) -> Report:
    """Assemble everything one solve produced."""
    names = _names(mentors, mentees)
    excluded = excluded or set()

    matches = tuple(
        MatchRow(
            mentor_key=assignment.mentor_key,
            mentor_name=names[assignment.mentor_key],
            mentee_key=assignment.mentee_key,
            mentee_name=names[assignment.mentee_key],
            percentage=assignment.score.percentage,
            scored_questions=assignment.score.scored_questions,
        )
        for assignment in solution.assignments
    )

    return Report(
        matches=matches,
        waitlist=tuple(
            build_waitlist(mentees, solution.unassigned, scores, excluded)
        ),
        blocking_pairs=tuple(
            find_blocking_pairs(
                mentors, mentees, scores, solution.assignments, excluded
            )
        ),
        avoid_blocks=tuple(avoid_blocks),
        review_flags=tuple(review_flags),
        cutoffs=tuple(cutoffs.values()),
        unfilled_slots=solution.unfilled_slots,
    )
