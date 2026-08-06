"""Scoring the location question on time difference rather than similarity.

Embeddings are useless here: every "city, state, country" string looks alike to
the model whether the two people are ten miles or ten thousand apart. What
actually decides whether a recurring meeting is possible is the time zone, so
each response is reduced to an offset in hours from Pacific Time and pairs are
scored on the gap between them.

Offsets are standard-time approximations. Daylight saving would shift some of
them by an hour, but the point bands are three hours wide, so it does not
change which band a pair lands in.
"""

import logging
import re
from dataclasses import dataclass

from app.config import (
    GOOD_MATCH_POINTS,
    LOCATION_GOOD_MAX_HOURS,
    LOCATION_PERFECT_MAX_HOURS,
    NO_MATCH_POINTS,
    PERFECT_MATCH_POINTS,
)
from app.normalize import is_blank, normalize
from app.questions import Question
from app.respondents import Respondent, ReviewFlag

logger = logging.getLogger(__name__)

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
) -> tuple[dict[str, LocationOffset], list[ReviewFlag]]:
    """Resolve everyone's location, flagging the ones that cannot be read."""
    offsets: dict[str, LocationOffset] = {}
    flags: list[ReviewFlag] = []

    for respondent in respondents:
        raw = respondent.responses.get(question.row, "")
        if is_blank(raw):
            continue

        offset = resolve_offset(raw)
        if offset is None:
            # Guessing at a location would silently distort the score, so it is
            # left unscored and handed to a coordinator instead.
            flags.append(
                ReviewFlag(
                    side=respondent.side,
                    respondent_key=respondent.key,
                    reason=f"could not read a time zone from {raw.strip()!r}",
                )
            )
            continue
        offsets[respondent.key] = offset

    logger.info(
        "resolved %d of %d locations", len(offsets), len(offsets) + len(flags)
    )
    return offsets, flags


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
