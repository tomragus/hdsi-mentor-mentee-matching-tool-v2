"""Scoring every pair, applying the avoid constraint, then assigning the cohort.

Three stages, in the order they run:

1. The five ways a question can be scored. Each scorer returns 10, 5, or 0 on
   the unweighted scale, or None when the question cannot be scored for a pair.
   None is not zero: those questions drop out of both the score and the
   denominator, so skipping an optional question costs nothing rather than
   lowering the ceiling a pair is measured against. Option-based questions score
   on option indices rather than text, which is what lets the feedback and
   mentoring-style rows work despite wording their options differently on each
   form. Semantic questions cannot use a fixed cutoff, so theirs are derived
   from the cohort's own distribution; location cannot use similarity at all.
2. Pair scoring. Every scored question contributes its points times its weight,
   less any write-in penalty, over the maximum achievable on the questions both
   parties answered. That ratio, not the raw total, is what ranking uses -- it
   stops pairs from placing higher merely for having had more opportunities to
   earn points.
3. The avoid constraint, then the assignment. Avoid answers are resolved once
   per person against a closed vocabulary drawn from the surveys themselves, and
   any pairing where one side works on what the other asked to avoid is removed
   from the matrix. What remains is solved globally rather than greedily: taking
   the highest pair, then the next, looks reasonable and is not, because an
   early pair claims a mentor a later mentee needed far more and the cohort ends
   up worse overall.
"""

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from app.config import (
    GOOD_MATCH_POINTS,
    LOCATION_GOOD_MAX_HOURS,
    LOCATION_PERFECT_MAX_HOURS,
    MAX_VOCABULARY_TERM_WORDS,
    MIN_VOCABULARY_TERM_LENGTH,
    NO_MATCH_POINTS,
    PERFECT_MATCH_POINTS,
    RANDOM_SEED,
    VOCABULARY_QUESTIONS,
    is_blank,
    normalize,
)
from app.inputs import (
    KIND_BLANK,
    MENTEE,
    MENTOR,
    ROLE_CHECKBOX,
    ROLE_LOCATION,
    ROLE_MULTIPLE_CHOICE,
    ROLE_SEMANTIC,
    ColumnLink,
    Question,
    Respondent,
    Response,
    build_cache,
    build_respondents,
    parse_responses,
    penalty,
    resolve_write_ins,
    similarity,
)

logger = logging.getLogger(__name__)


# --- 1. scoring one question --------------------------------------------------

def score_multiple_choice(
    question: Question, mentor: Response, mentee: Response
) -> int | None:
    """Look the pair of chosen options up in this row's criteria table."""
    if not mentor.indices or not mentee.indices or not question.choice_scores:
        return None

    # A multiple choice answer is a single option; a resolved write-in leaves
    # exactly one index too.
    pair = (mentor.indices[0], mentee.indices[0])
    points = question.choice_scores.get(pair)
    if points is None:
        # The table is built from both orderings of every stated combination, so
        # a miss means the criteria simply do not mention this pairing.
        logger.warning(
            "row %d: no criteria for option pair %s, scoring it as no match",
            question.row,
            pair,
        )
        return NO_MATCH_POINTS
    return points


def score_checkbox(question: Question, mentor: Response, mentee: Response) -> int | None:
    """Score on how many options the two selected in common."""
    if not mentor.indices or not mentee.indices or not question.overlap_thresholds:
        return None

    overlap = len(set(mentor.indices) & set(mentee.indices))
    # Thresholds are held highest-first, so the first one met is the best one.
    for minimum, points in question.overlap_thresholds:
        if overlap >= minimum:
            return points
    return NO_MATCH_POINTS


@dataclass(frozen=True)
class Cutoffs:
    """One question's derived similarity thresholds. Plain immutable record."""

    row: int
    percentiles: tuple[int, int]
    upper: float
    lower: float
    pair_count: int


def _answered(answers: dict[int, Response], row: int) -> bool:
    """Whether this side gave a usable answer to one question."""
    response = answers.get(row)
    return (
        response is not None
        and response.kind != KIND_BLANK
        and bool(response.text.strip())
    )


def similarities(
    question: Question,
    mentor_answers: Iterable[dict[int, Response]],
    mentee_answers: Iterable[dict[int, Response]],
    cache: dict[str, np.ndarray],
) -> list[float]:
    """Cosine similarity for every pair where both sides answered this question."""
    mentors = [a for a in mentor_answers if _answered(a, question.row)]
    mentees = [a for a in mentee_answers if _answered(a, question.row)]
    return [
        similarity(cache, mentor[question.row].text, mentee[question.row].text)
        for mentor in mentors
        for mentee in mentees
    ]


def calibrate(
    questions: list[Question],
    mentor_answers: list[dict[int, Response]],
    mentee_answers: list[dict[int, Response]],
    cache: dict[str, np.ndarray],
) -> dict[int, Cutoffs]:
    """Derive each semantic question's cutoffs from this cohort's own scores."""
    derived: dict[int, Cutoffs] = {}

    for question in questions:
        if question.role != ROLE_SEMANTIC:
            continue

        values = similarities(question, mentor_answers, mentee_answers, cache)
        if not values:
            # Nobody on one side answered, so there is no distribution to read
            # cutoffs from and the question drops out for every pair.
            logger.warning("row %d: no answered pairs, question not scored", question.row)
            continue

        upper_pct, lower_pct = question.percentiles
        upper, lower = np.percentile(values, [upper_pct, lower_pct])
        derived[question.row] = Cutoffs(
            row=question.row,
            percentiles=question.percentiles,
            upper=float(upper),
            lower=float(lower),
            pair_count=len(values),
        )
        logger.info(
            "row %d: %d/%d percentile cutoffs are %.3f/%.3f over %d pairs",
            question.row,
            upper_pct,
            lower_pct,
            upper,
            lower,
            len(values),
        )

    return derived


def score_semantic(
    question: Question,
    mentor: Response,
    mentee: Response,
    cache: dict[str, np.ndarray],
    cutoffs: dict[int, Cutoffs],
) -> int | None:
    """Score one pair on one semantic question against its derived cutoffs."""
    derived = cutoffs.get(question.row)
    if derived is None:
        return None
    if mentor.kind == KIND_BLANK or mentee.kind == KIND_BLANK:
        return None
    if not mentor.text.strip() or not mentee.text.strip():
        return None

    value = similarity(cache, mentor.text, mentee.text)
    if value >= derived.upper:
        return PERFECT_MATCH_POINTS
    if value >= derived.lower:
        return GOOD_MATCH_POINTS
    return NO_MATCH_POINTS


# Hours ahead of Pacific Time. Cities are listed alongside states and countries
# because respondents often give only one of the three.
_ZONES_BY_NAME: dict[float, tuple[str, ...]] = {
    -2: ("hawaii", "honolulu"),
    -1: ("alaska", "anchorage"),
    0: (
        "california", "washington", "oregon", "nevada", "baja california",
        "san diego", "la jolla", "los angeles", "san francisco", "san jose",
        "irvine", "sacramento", "oakland", "berkeley", "palo alto", "pasadena",
        "santa monica", "long beach", "fresno", "santa barbara", "seattle",
        "redmond", "bellevue", "tacoma", "spokane", "portland", "eugene",
        "las vegas", "reno", "vancouver", "tijuana",
    ),
    1: (
        "arizona", "colorado", "utah", "new mexico", "montana", "idaho",
        "wyoming", "denver", "boulder", "colorado springs", "phoenix", "tucson",
        "scottsdale", "salt lake city", "provo", "albuquerque", "santa fe",
        "boise", "calgary", "edmonton",
    ),
    2: (
        "texas", "illinois", "minnesota", "missouri", "louisiana", "wisconsin",
        "iowa", "nebraska", "kansas", "oklahoma", "arkansas", "alabama",
        "mississippi", "north dakota", "south dakota", "tennessee", "manitoba",
        "austin", "dallas", "houston", "san antonio", "fort worth", "chicago",
        "minneapolis", "st paul", "st louis", "kansas city", "milwaukee",
        "madison", "new orleans", "baton rouge", "nashville", "memphis",
        "omaha", "oklahoma city", "winnipeg", "mexico", "mexico city",
        "guadalajara", "monterrey",
    ),
    3: (
        "new york", "massachusetts", "georgia", "florida", "pennsylvania",
        "virginia", "north carolina", "south carolina", "ohio", "michigan",
        "new jersey", "maryland", "connecticut", "maine", "vermont",
        "new hampshire", "rhode island", "delaware", "west virginia",
        "indiana", "kentucky", "district of columbia", "ontario", "quebec",
        "new york city", "brooklyn", "manhattan", "queens", "boston",
        "cambridge", "atlanta", "miami", "orlando", "tampa", "jacksonville",
        "philadelphia", "pittsburgh", "detroit", "ann arbor", "columbus",
        "cleveland", "cincinnati", "baltimore", "washington dc", "raleigh",
        "durham", "charlotte", "richmond", "buffalo", "toronto", "ottawa",
        "montreal", "peru", "lima", "colombia", "bogota",
    ),
    5: (
        "brazil", "sao paulo", "rio de janeiro", "brasilia", "argentina",
        "buenos aires", "chile", "santiago", "uruguay", "montevideo",
    ),
    8: (
        "united kingdom", "england", "scotland", "wales", "london",
        "manchester", "edinburgh", "glasgow", "ireland", "dublin", "portugal",
        "lisbon", "iceland", "reykjavik", "morocco", "casablanca", "ghana",
        "accra", "senegal", "dakar",
    ),
    9: (
        "germany", "berlin", "munich", "hamburg", "frankfurt", "france",
        "paris", "lyon", "spain", "madrid", "barcelona", "italy", "rome",
        "milan", "netherlands", "amsterdam", "rotterdam", "switzerland",
        "zurich", "geneva", "sweden", "stockholm", "norway", "oslo", "denmark",
        "copenhagen", "poland", "warsaw", "krakow", "austria", "vienna",
        "belgium", "brussels", "czech republic", "prague", "hungary",
        "budapest", "nigeria", "lagos", "abuja",
    ),
    10: (
        "greece", "athens", "finland", "helsinki", "israel", "tel aviv",
        "jerusalem", "south africa", "johannesburg", "cape town", "egypt",
        "cairo", "romania", "bucharest", "ukraine", "kyiv", "kiev", "bulgaria",
        "sofia",
    ),
    11: (
        "turkey", "istanbul", "ankara", "russia", "moscow", "kenya", "nairobi",
        "saudi arabia", "riyadh", "jeddah", "ethiopia", "addis ababa", "qatar",
        "doha", "iraq", "baghdad",
    ),
    12: ("united arab emirates", "dubai", "abu dhabi", "oman", "muscat", "azerbaijan", "baku"),
    13: ("pakistan", "karachi", "lahore", "islamabad", "uzbekistan", "tashkent"),
    13.5: (
        "india", "bangalore", "bengaluru", "mumbai", "delhi", "new delhi",
        "chennai", "hyderabad", "pune", "kolkata", "ahmedabad", "sri lanka",
        "colombo",
    ),
    14: ("bangladesh", "dhaka", "nepal", "kathmandu"),
    15: (
        "thailand", "bangkok", "vietnam", "hanoi", "ho chi minh city",
        "indonesia", "jakarta", "myanmar", "yangon",
    ),
    16: (
        "china", "beijing", "shanghai", "shenzhen", "guangzhou", "hong kong",
        "singapore", "taiwan", "taipei", "philippines", "manila", "malaysia",
        "kuala lumpur", "perth",
    ),
    17: ("japan", "tokyo", "osaka", "kyoto", "korea", "south korea", "seoul", "busan"),
    18: ("australia", "sydney", "melbourne", "brisbane", "canberra", "adelaide"),
    20: ("new zealand", "auckland", "wellington"),
}

# Two-letter codes are matched only as a whole comma-separated segment. As
# substrings they would be a disaster: "LA" is Louisiana in "New Orleans, LA",
# "IN" and "OR" are ordinary English words, and "DE" appears inside many names.
_ZONES_BY_CODE: dict[float, tuple[str, ...]] = {
    -2: ("hi",),
    -1: ("ak",),
    0: ("ca", "wa", "or", "nv", "bc"),
    1: ("az", "co", "ut", "nm", "mt", "id", "wy", "ab"),
    2: ("tx", "il", "mn", "mo", "la", "wi", "ia", "ne", "ks", "ok", "ar", "al", "ms", "nd", "sd", "tn"),
    3: ("ny", "ma", "ga", "fl", "pa", "va", "nc", "sc", "oh", "mi", "nj", "md", "ct", "me", "vt", "nh", "ri", "de", "wv", "in", "ky", "dc", "on", "qc"),
    8: ("uk",),
}

_OFFSET_BY_NAME = {
    name: hours for hours, names in _ZONES_BY_NAME.items() for name in names
}
_OFFSET_BY_CODE = {
    code: hours for hours, codes in _ZONES_BY_CODE.items() for code in codes
}

# Longest first, so "washington dc" is found before "washington state" fails to
# and "new york city" before "new york".
_NAMES_LONGEST_FIRST = sorted(_OFFSET_BY_NAME, key=len, reverse=True)

# "3 hours ahead of Pacific", "1 hour behind"
_SPELLED_OUT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b[^,;]{0,24}?\b(ahead|behind|earlier|later)"
)
# "(+2)", "-3 hours", "+3 from san diego". Capped at two digits and forbidden
# from running into a third, so the "-1234" of a zip code is not read as an
# offset of minus twelve hundred.
_SIGNED = re.compile(r"([+-])\s*(\d{1,2}(?:\.\d+)?)(?!\d)")

_SEGMENTS = re.compile(r"[,/()\[\]–—-]+")

# Washington is a state on the west coast and a city on the east. Whichever
# segment the "DC" lands in, it settles which one was meant.
_DISTRICT = re.compile(r"\b(d ?\.? ?c\.?|district of columbia)\b")

# No real answer to this question is more than a day away.
_MAX_PLAUSIBLE_HOURS = 24


@dataclass(frozen=True)
class LocationOffset:
    """A resolved location. Plain immutable record."""

    hours: float
    # "stated" when the respondent gave the difference themselves, "lookup"
    # when it came from the table. Useful when reviewing a surprising match.
    source: str


def _stated_offset(text: str) -> float | None:
    """Read a time difference the respondent gave explicitly."""
    spelled = _SPELLED_OUT.search(text)
    if spelled:
        hours = float(spelled.group(1))
        hours = -hours if spelled.group(2) in ("behind", "earlier") else hours
    else:
        signed = _SIGNED.search(text)
        if not signed:
            return None
        hours = float(signed.group(2))
        hours = -hours if signed.group(1) == "-" else hours

    return hours if abs(hours) <= _MAX_PLAUSIBLE_HOURS else None


def _looked_up_offset(text: str) -> float | None:
    """Resolve a place name against the table, most specific part first."""
    if _DISTRICT.search(text):
        return _OFFSET_BY_NAME["district of columbia"]

    segments = [segment.strip() for segment in _SEGMENTS.split(text)]
    for segment in segments:
        if segment in _OFFSET_BY_NAME:
            return _OFFSET_BY_NAME[segment]
        if segment in _OFFSET_BY_CODE:
            return _OFFSET_BY_CODE[segment]

    # Nothing matched a segment cleanly, so fall back to finding a place name
    # anywhere in the text, as in "I live just outside Bangalore".
    for name in _NAMES_LONGEST_FIRST:
        if re.search(rf"\b{re.escape(name)}\b", text):
            return _OFFSET_BY_NAME[name]
    return None


def resolve_offset(raw: str) -> LocationOffset | None:
    """Hours from Pacific Time for one response, or None if unrecognizable."""
    if is_blank(raw):
        return None

    text = normalize(raw)
    stated = _stated_offset(text)
    if stated is not None:
        return LocationOffset(hours=stated, source="stated")

    looked_up = _looked_up_offset(text)
    if looked_up is not None:
        return LocationOffset(hours=looked_up, source="lookup")
    return None


def resolve_offsets(
    question: Question, respondents: list[Respondent]
) -> dict[str, LocationOffset]:
    """Resolve everyone's location, skipping the ones that cannot be read."""
    offsets: dict[str, LocationOffset] = {}
    unread = 0

    for respondent in respondents:
        raw = respondent.responses.get(question.row, "")
        if is_blank(raw):
            continue

        offset = resolve_offset(raw)
        if offset is None:
            # Guessing at a location would silently distort the score, so the
            # question is simply left unscored for this person. Noted in the
            # log rather than raised to the coordinator.
            logger.info("could not read a time zone from %r", raw.strip())
            unread += 1
            continue
        offsets[respondent.key] = offset

    logger.info("resolved %d of %d locations", len(offsets), len(offsets) + unread)
    return offsets


def score_location(
    mentor_key: str, mentee_key: str, offsets: dict[str, LocationOffset]
) -> int | None:
    """Score a pair on the gap between their offsets."""
    mentor, mentee = offsets.get(mentor_key), offsets.get(mentee_key)
    if mentor is None or mentee is None:
        return None

    difference = abs(mentor.hours - mentee.hours)
    if difference <= LOCATION_PERFECT_MAX_HOURS:
        return PERFECT_MATCH_POINTS
    if difference <= LOCATION_GOOD_MAX_HOURS:
        return GOOD_MATCH_POINTS
    return NO_MATCH_POINTS


# --- 2. scoring one pair --------------------------------------------------

# Roles that earn points. The avoid row is excluded deliberately: it is a
# constraint on which pairings are allowed, not a measure of how well two
# people fit.
SCORED_ROLES = (ROLE_MULTIPLE_CHOICE, ROLE_CHECKBOX, ROLE_SEMANTIC, ROLE_LOCATION)


@dataclass(frozen=True)
class Participant:
    """A respondent together with their parsed answers. Plain immutable record."""

    respondent: Respondent
    answers: dict[int, Response]


@dataclass(frozen=True)
class ScoringContext:
    """Everything derived from the cohort as a whole, computed once per run.

    Plain immutable record. Cutoffs and location offsets both depend on who
    submitted this cycle, so they cannot be computed per pair.
    """

    questions: list[Question]
    cache: dict[str, np.ndarray]
    cutoffs: dict[int, Cutoffs]
    offsets: dict[str, LocationOffset]


@dataclass(frozen=True)
class QuestionScore:
    """One question's contribution to one pair. Plain immutable record."""

    row: int
    points: int
    penalty: int
    contribution: int
    maximum: int


@dataclass(frozen=True)
class PairScore:
    """One mentor/mentee combination's compatibility. Plain immutable record."""

    mentor_key: str
    mentee_key: str
    raw: int
    maximum: int
    # raw / maximum. Can be negative, since write-in penalties come off the raw
    # total and nothing clamps it.
    normalized: float
    question_scores: tuple[QuestionScore, ...]

    @property
    def percentage(self) -> float:
        return self.normalized * 100

    @property
    def scored_questions(self) -> int:
        """How many questions this score rests on, out of those both were asked."""
        return len(self.question_scores)


def _points_for(
    context: ScoringContext,
    question: Question,
    mentor: Participant,
    mentee: Participant,
) -> int | None:
    """Route one question to its scorer, or None if this pair cannot be scored."""
    if question.role == ROLE_LOCATION:
        return score_location(
            mentor.respondent.key, mentee.respondent.key, context.offsets
        )

    mentor_answer = mentor.answers.get(question.row)
    mentee_answer = mentee.answers.get(question.row)
    if mentor_answer is None or mentee_answer is None:
        return None

    if question.role == ROLE_SEMANTIC:
        return score_semantic(
            question, mentor_answer, mentee_answer, context.cache, context.cutoffs
        )
    if question.role == ROLE_MULTIPLE_CHOICE:
        return score_multiple_choice(question, mentor_answer, mentee_answer)
    if question.role == ROLE_CHECKBOX:
        return score_checkbox(question, mentor_answer, mentee_answer)
    return None


def score_pair(
    context: ScoringContext, mentor: Participant, mentee: Participant
) -> PairScore:
    """Score one mentor against one mentee across every scored question."""
    scores: list[QuestionScore] = []

    for question in context.questions:
        if question.weight == 0 or question.role not in SCORED_ROLES:
            continue

        points = _points_for(context, question, mentor, mentee)
        if points is None:
            # Neither the points nor the maximum count, so an unanswered
            # question leaves the pair's ratio untouched.
            continue

        mentor_answer = mentor.answers.get(question.row)
        mentee_answer = mentee.answers.get(question.row)
        charged = (
            penalty(mentor_answer, mentee_answer)
            if mentor_answer and mentee_answer
            else 0
        )
        weighted = points * question.weight
        scores.append(
            QuestionScore(
                row=question.row,
                points=points,
                penalty=charged,
                # The penalty comes off after the multiplier, so it costs the
                # same on a weight-3 question as on a weight-1 one.
                contribution=weighted - charged,
                maximum=PERFECT_MATCH_POINTS * question.weight,
            )
        )

    raw = sum(score.contribution for score in scores)
    maximum = sum(score.maximum for score in scores)
    return PairScore(
        mentor_key=mentor.respondent.key,
        mentee_key=mentee.respondent.key,
        raw=raw,
        maximum=maximum,
        # A pair with nothing in common to score on has no ratio to speak of.
        normalized=raw / maximum if maximum else 0.0,
        question_scores=tuple(scores),
    )


def score_all(
    context: ScoringContext, mentors: list[Participant], mentees: list[Participant]
) -> dict[tuple[str, str], PairScore]:
    """Score the full mentor x mentee matrix, keyed by the pair's two keys."""
    scores = {
        (mentor.respondent.key, mentee.respondent.key): score_pair(
            context, mentor, mentee
        )
        for mentor in mentors
        for mentee in mentees
    }
    logger.info("scored %d mentor/mentee pairs", len(scores))
    return scores


def prepare(
    questions: list[Question],
    links: dict[int, ColumnLink],
    mentor_frame,
    mentee_frame,
) -> tuple[list[Participant], list[Participant], ScoringContext]:
    """Run everything that happens before pair scoring, in order.

    Deduplicate, parse, embed once, resolve write-ins, then derive the
    cohort-wide values -- similarity cutoffs and time zone offsets -- that
    individual pair scores are measured against.
    """
    mentors = build_respondents(questions, links, mentor_frame, MENTOR)
    mentees = build_respondents(questions, links, mentee_frame, MENTEE)

    mentor_answers = [parse_responses(questions, person) for person in mentors]
    mentee_answers = [parse_responses(questions, person) for person in mentees]

    cache = build_cache(questions, mentor_answers + mentee_answers)
    mentor_answers = [
        resolve_write_ins(questions, MENTOR, answers, cache) for answers in mentor_answers
    ]
    mentee_answers = [
        resolve_write_ins(questions, MENTEE, answers, cache) for answers in mentee_answers
    ]

    cutoffs = calibrate(questions, mentor_answers, mentee_answers, cache)

    location = next((q for q in questions if q.role == ROLE_LOCATION), None)
    offsets = resolve_offsets(location, mentors + mentees) if location else {}

    context = ScoringContext(
        questions=questions, cache=cache, cutoffs=cutoffs, offsets=offsets
    )
    return (
        [Participant(person, answers) for person, answers in zip(mentors, mentor_answers)],
        [Participant(person, answers) for person, answers in zip(mentees, mentee_answers)],
        context,
    )


# --- 3. the avoid constraint ----------------------------------------------

# An extractor takes the answer and the vocabulary and returns the terms it
# found, or None if it could not produce an answer at all.
Extractor = Callable[[str, tuple[str, ...]], set[str] | None]

# Ways of saying "nothing to avoid". Anything longer is left to the extractor,
# which will resolve prose like "no topics I'd prefer to avoid" to no terms.
NULL_ANSWERS = frozenset(
    {
        "", "-", "--", ".", "n/a", "na", "no", "nope", "none", "nothing",
        "no thanks", "not really", "not at all", "no preference",
        "no preferences", "nil", "n.a.", "none that i can think of",
    }
)

# Words that are structure or filler rather than a term worth matching on.
JUNK_TERMS = frozenset(
    {
        "etc", "e.g", "eg", "ie", "other", "others", "option", "options",
        "check box", "check boxes", "checkbox", "maybe", "and", "or", "the",
        "data", "tech", "misc", "various", "many", "any", "all", "none",
        "more", "some", "select up to 3", "select up to",
    }
)

# Inline enumeration, as in "1. AI Agents 2. NLP 3. MLOps".
_ENUMERATION = re.compile(r"(?:^|\s)\d+\s*[.)]\s*")
_TERM_SEPARATORS = re.compile(r"[,;/&\n|]+")
# The example lists the questions give, as in "(Python, R, TensorFlow, etc.)".
_PARENTHETICAL = re.compile(r"\(([^)]*)\)")


def _clean_terms(text: str) -> list[str]:
    """Split one cell into candidate vocabulary terms."""
    if is_blank(text):
        return []

    # Parentheses hold sub-lists ("Python (pandas, scikit-learn)"), so they
    # separate terms rather than enclosing one.
    flattened = _ENUMERATION.sub(", ", text.replace("(", ",").replace(")", ","))

    terms = []
    for part in _TERM_SEPARATORS.split(flattened):
        term = normalize(part).strip(" .-'\"")
        if not term or "?" in term:
            continue
        if len(term) < MIN_VOCABULARY_TERM_LENGTH:
            continue
        if len(term.split()) > MAX_VOCABULARY_TERM_WORDS:
            continue
        if term in JUNK_TERMS or term.isdigit():
            continue
        terms.append(term)
    return terms


def _vocabulary_questions(questions: list[Question]) -> list[Question]:
    return [
        question
        for question in questions
        if any(
            keyword in normalize(question.mentor_question)
            for keyword in VOCABULARY_QUESTIONS
        )
    ]


def build_vocabulary(
    questions: list[Question], respondents: Iterable[Respondent]
) -> tuple[str, ...]:
    """Collect every industry, sub-domain, and tool this cohort mentioned.

    Both sources count: the examples the questions themselves give, and what
    people actually wrote.
    """
    rows = _vocabulary_questions(questions)
    terms: set[str] = set()

    for question in rows:
        for wording in (question.mentor_question, question.mentee_question):
            for examples in _PARENTHETICAL.findall(wording or ""):
                terms.update(_clean_terms(examples))

    for respondent in respondents:
        for question in rows:
            terms.update(_clean_terms(respondent.responses.get(question.row, "")))

    vocabulary = tuple(sorted(terms))
    logger.info("controlled vocabulary holds %d terms", len(vocabulary))
    return vocabulary


def is_null_answer(text: str) -> bool:
    """Whether an answer means "nothing to avoid"."""
    return normalize(text).strip(" .!") in NULL_ANSWERS


def keyword_extractor(text: str, vocabulary: tuple[str, ...]) -> set[str]:
    """Find vocabulary terms stated outright in the answer."""
    haystack = normalize(text)
    return {
        term
        for term in vocabulary
        if re.search(rf"\b{re.escape(term)}\b", haystack)
    }


def extract_avoid_terms(
    question: Question,
    respondents: Iterable[Respondent],
    vocabulary: tuple[str, ...],
    extractor: Extractor = keyword_extractor,
) -> dict[str, set[str]]:
    """Resolve everyone's avoid answer, once per respondent rather than per pair."""
    extracted: dict[str, set[str]] = {}

    for respondent in respondents:
        answer = respondent.responses.get(question.row, "")
        if is_blank(answer) or is_null_answer(answer):
            continue

        terms = extractor(answer, vocabulary)
        if terms is None:
            # A failed extraction must not stop the run, so the answer is left
            # with no terms and nothing is blocked for this person.
            logger.warning(
                "could not extract avoid terms from %r", answer.strip()
            )
            continue

        if terms:
            extracted[respondent.key] = terms
            logger.info(
                "%s %s wants to avoid %s",
                respondent.side,
                respondent.key,
                ", ".join(sorted(terms)),
            )

    return extracted


def stated_terms_for_all(
    questions: list[Question],
    respondents: Iterable[Respondent],
    vocabulary: tuple[str, ...],
) -> dict[str, set[str]]:
    """The vocabulary terms each respondent said they work on.

    The question list and the vocabulary are the same for everyone, so both are
    resolved once rather than per respondent.
    """
    known = set(vocabulary)
    rows = [question.row for question in _vocabulary_questions(questions)]

    stated = {}
    for respondent in respondents:
        terms: set[str] = set()
        for row in rows:
            terms.update(_clean_terms(respondent.responses.get(row, "")))
        stated[respondent.key] = terms & known
    return stated


def blocked_cells(
    mentors: Iterable[Respondent],
    mentees: Iterable[Respondent],
    avoid_terms: dict[str, set[str]],
    stated: dict[str, set[str]],
) -> set[tuple[str, str]]:
    """The (mentor, mentee) keys to leave out of the assignment matrix.

    Checked in both directions: a mentee's preference rules a mentor out just
    as firmly as the reverse.
    """
    blocked = set()
    for mentor in mentors:
        mentor_avoids = avoid_terms.get(mentor.key, set())
        for mentee in mentees:
            mentee_avoids = avoid_terms.get(mentee.key, set())
            if mentor_avoids & stated.get(mentee.key, set()) or (
                mentee_avoids & stated.get(mentor.key, set())
            ):
                blocked.add((mentor.key, mentee.key))

    logger.info("avoid constraint blocked %d pairs", len(blocked))
    return blocked


# --- 4. the assignment ----------------------------------------------------

# Low enough that the solver will always prefer waitlisting a mentee, but
# finite, so a fully blocked mentee cannot make the problem unsolvable.
BLOCKED_SCORE = -1.0e6
# Smaller than any meaningful difference between two scores, so it only decides
# exact ties.
TIE_BREAK_RANGE = 1.0e-9


@dataclass(frozen=True)
class Assignment:
    """One mentor paired with one mentee. Plain immutable record."""

    mentor_key: str
    mentee_key: str
    score: PairScore


@dataclass(frozen=True)
class Solution:
    """The result of one solve. Plain immutable record."""

    assignments: tuple[Assignment, ...]
    # Mentee keys that ended up on a dummy column.
    unassigned: tuple[str, ...]


def build_slots(mentors: list[Participant], mentee_count: int) -> list[str]:
    """One mentor key per opening the solver is allowed to fill.

    A mentor who offered two mentees normally appears twice, so their openings
    can be filled independently.

    When there are at least as many mentors as mentees, everybody is capped at
    one instead. Their two columns are identical, so nothing in the solve
    prefers spreading, and being attractive is a property of the mentor rather
    than the slot: a popular mentor's openings both fill while another mentor
    gets nobody. Capping only when there are mentors to spare means no mentee
    is waitlisted for it -- one each still reaches everyone.
    """
    spare_mentors = len(mentors) >= mentee_count
    return [
        mentor.respondent.key
        for mentor in mentors
        for _ in range(1 if spare_mentors else max(1, mentor.respondent.capacity))
    ]


def build_matrix(
    mentees: list[Participant],
    slots: list[str],
    scores: dict[tuple[str, str], PairScore],
    blocked: set[tuple[str, str]],
) -> np.ndarray:
    """Build the square score matrix the solver works on.

    Rows are mentees and columns are mentor slots, both padded with dummies so
    the matrix is square and every row can be assigned somewhere.
    """
    size = max(len(mentees), len(slots))
    # Dummy rows and columns stay at zero: an unfilled slot and a waitlisted
    # mentee are both worth nothing rather than negative.
    matrix = np.zeros((size, size), dtype=float)

    for row, mentee in enumerate(mentees):
        mentee_key = mentee.respondent.key
        for column, mentor_key in enumerate(slots):
            pair = (mentor_key, mentee_key)
            score = None if pair in blocked else scores.get(pair)
            matrix[row, column] = score.normalized if score else BLOCKED_SCORE

    # Seeded jitter, so two identical scores resolve the same way on every run
    # of the same inputs rather than however the solver happens to break ties.
    rng = np.random.default_rng(RANDOM_SEED)
    return matrix + rng.uniform(0, TIE_BREAK_RANGE, size=matrix.shape)


def solve(
    mentors: list[Participant],
    mentees: list[Participant],
    scores: dict[tuple[str, str], PairScore],
    blocked: set[tuple[str, str]] | None = None,
) -> Solution:
    """Assign mentees to mentor slots, maximizing total compatibility."""
    slots = build_slots(mentors, len(mentees))
    if not slots or not mentees:
        return Solution(
            assignments=(),
            unassigned=tuple(m.respondent.key for m in mentees),
        )

    matrix = build_matrix(mentees, slots, scores, blocked or set())
    rows, columns = linear_sum_assignment(matrix, maximize=True)

    assignments = []
    assigned_mentees = set()

    for row, column in zip(rows, columns):
        if row >= len(mentees) or column >= len(slots):
            # One side of this pairing is padding, so nothing was matched.
            continue
        mentee_key = mentees[row].respondent.key
        mentor_key = slots[column]
        if matrix[row, column] <= BLOCKED_SCORE / 2:
            # Happens whenever there are at least as many slots as mentees and
            # this mentee is blocked from every one they landed on.
            logger.warning("no permitted mentor for mentee %s", mentee_key)
            continue

        assignments.append(
            Assignment(
                mentor_key=mentor_key,
                mentee_key=mentee_key,
                score=scores[(mentor_key, mentee_key)],
            )
        )
        assigned_mentees.add(mentee_key)

    assignments.sort(key=lambda item: item.score.normalized, reverse=True)
    unassigned = tuple(
        mentee.respondent.key
        for mentee in mentees
        if mentee.respondent.key not in assigned_mentees
    )

    logger.info(
        "assigned %d of %d mentees across %d slots",
        len(assignments),
        len(mentees),
        len(slots),
    )
    return Solution(
        assignments=tuple(assignments),
        unassigned=unassigned,
    )
