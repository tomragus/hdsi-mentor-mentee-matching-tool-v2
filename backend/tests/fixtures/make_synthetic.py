"""Generate two synthetic cohorts, between them exercising the whole pipeline.

The real sample exports are only 6 mentors and 4 mentees, which leaves several
code paths unreachable: mentor slots outstrip mentees so nobody is waitlisted,
almost nobody answers the avoid question so the hard constraint never fires,
and percentile calibration runs over as few as four pairs.

Two cohorts are written rather than one, with deliberately different shapes:

    A  16 mentors / 48 mentees -- oversubscribed, so mentees go unmatched
    B  24 mentors / 18 mentees -- undersubscribed, so mentors go unused

Each cohort is filled at random from the answer pools below, then a fixed table
of edge cases is written over the first sixteen rows so the awkward inputs are
guaranteed to appear rather than left to the seed. See EDGE_CASES for what each
row demonstrates.

These are test fixtures, not an evaluation set -- the free text is drawn from
curated pools and says nothing about whether a real match would be any good.

Column headers come from the questions database itself, so a change there flows
into the fixtures rather than silently breaking the link between them.

Writes to the repository root, next to the real exports. Regenerate with:

    uv run python tests/fixtures/make_synthetic.py
"""

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import pandas as pd

from app.inputs import (
    MENTEE,
    MENTOR,
    Question,
    ROLE_AVOID,
    ROLE_CHECKBOX,
    ROLE_LOCATION,
    ROLE_MULTIPLE_CHOICE,
    ROLE_SEMANTIC,
    load_questions,
)

# One seed per cohort, so regenerating either one leaves the other unchanged.
COHORTS = (
    {"tag": "A", "seed": 4242, MENTOR: 16, MENTEE: 48},
    {"tag": "B", "seed": 9317, MENTOR: 24, MENTEE: 18},
)

ROOT = Path(__file__).parents[3]
DATABASE = ROOT / "Mentee_Mentor Questions Database.csv"

FORM_NAMES = {MENTOR: "Alumni Mentor", MENTEE: "Student Mentee"}

# Database row numbers, in the order the database lists them.
COMMITMENT_ROW, COMMUNICATION_ROW, TOPICS_ROW = 1, 2, 3
SHADOW_ROW, PROJECT_ROW = 4, 5
INDUSTRY_ROW, LOCATION_ROW, MOTIVATION_ROW = 6, 7, 8
FEEDBACK_ROW, COLLABORATION_ROW, MENTORING_STYLE_ROW = 9, 10, 11
AVOID_ROW, SUBDOMAIN_ROW, TOOLS_ROW = 12, 13, 14
STYLE_ROW, HOBBIES_ROW = 15, 16
LANGUAGE_ROW, ANYTHING_ELSE_ROW = 17, 18
NAME_ROW, EMAIL_ROW, GRADUATION_ROW = 19, 20, 21
PROGRAM_ROW, JOB_ROW, CAPACITY_ROW = 22, 23, 24


# --- 1. answer pools ------------------------------------------------------

FIRST_NAMES = [
    "Amara", "Ben", "Chloe", "Diego", "Elena", "Farid", "Grace", "Hana",
    "Ibrahim", "Jonas", "Kavya", "Liam", "Mei", "Nadia", "Omar", "Priya",
    "Quinn", "Rafael", "Sofia", "Tomas", "Uma", "Viktor", "Wren", "Xiulan",
    "Yusuf", "Zara", "Aditi", "Bruno", "Cara", "Dmitri", "Esi", "Freya",
    "Gustavo", "Hiro", "Ines", "Jamal", "Kiran", "Lucia", "Mateo", "Noor",
    "Oscar", "Paloma", "Ravi", "Sena", "Tariq", "Ursula", "Vikram", "Wei",
    "Yara", "Zoltan", "Anika", "Bilal", "Clara", "Devon", "Emeka", "Fatima",
    "Giulia", "Hassan", "Ilya", "Junko", "Khalid", "Leila", "Milos", "Nia",
]

LAST_NAMES = [
    "Okafor", "Nakamura", "Silva", "Ferreira", "Kowalski", "Haddad", "Osei",
    "Lindqvist", "Rahman", "Petrov", "Iyer", "Murphy", "Zhang", "Karimi",
    "Diallo", "Sharma", "OBrien", "Castillo", "Rossi", "Novak", "Bauer",
    "Tanaka", "Mensah", "Reyes", "Andersen", "Volkov", "Chowdhury", "Marino",
    "Dubois", "Fitzgerald", "Nguyen", "Adeyemi", "Salazar", "Bergstrom",
    "Alkhatib", "Popescu", "Yilmaz", "Choudhury", "Barros", "Lindgren",
    "Aparicio", "Bhatt", "Cisneros", "Delacroix", "Eriksen", "Fontaine",
]

# Written to reach every branch of the time zone parser: the lookup table by
# name and by two-letter code, the stated-difference patterns, and the answers
# that resolve to nothing at all.
LOCATIONS = [
    # Same hour as Pacific.
    "San Diego, California, USA", "La Jolla, CA", "Los Angeles, CA, USA",
    "San Francisco, California", "Seattle, WA", "Portland, Oregon, USA",
    "Irvine, CA 92617", "Las Vegas, Nevada, USA", "Vancouver, BC, Canada",
    "Tijuana, Baja California, Mexico",
    # One hour ahead.
    "Denver, Colorado, USA", "Phoenix, AZ", "Salt Lake City, Utah", "Boise, ID",
    # Two hours ahead, which is the edge of a partial location score.
    "Austin, Texas, USA", "Chicago, IL", "Minneapolis, Minnesota, USA",
    "Mexico City, Mexico",
    # Three or more, which scores nothing.
    "New York, NY", "Boston, Massachusetts, USA", "Atlanta, GA, USA",
    "Toronto, Ontario, Canada", "Miami, FL", "London, United Kingdom",
    "Berlin, Germany", "Lagos, Nigeria", "Bangalore, India", "Singapore",
    "Tokyo, Japan", "Seoul, South Korea", "Sydney, Australia",
    "Sao Paulo, Brazil", "Dubai, United Arab Emirates",
    # No clean segment to match, so only the substring pass finds these.
    "I live just outside Bangalore most of the year",
    "Somewhere near Austin these days",
    # Two-letter codes that would be a disaster read as city names.
    "New Orleans, LA", "Washington, DC", "Portland, ME",
    # Differences the respondent stated outright, read off the text instead.
    "Remote, 3 hours ahead of Pacific Time",
    "Boulder, Colorado (1 hour ahead of Pacific)",
    "Currently in Lisbon, 8 hours ahead",
    "Honolulu, HI (2 hours behind Pacific)",
    "Anywhere, really (+2)",
    "Hybrid between two offices (-1)",
    # Unreadable, so the question drops out for this person.
    "Currently travelling", "Prefer not to say", "Remote", "It varies",
]

SHARED_POOLS = {
    INDUSTRY_ROW: [
        "Technology", "Software", "Finance", "Investment banking",
        "Management consulting", "Healthcare", "Biotechnology",
        "Pharmaceuticals", "Video games", "Climate and sustainability",
        "Education technology", "Government and public policy", "Retail",
        "E-commerce", "Aerospace", "Media and entertainment", "Nonprofit",
        "Insurance", "Agriculture technology", "Autonomous vehicles",
        "Cybersecurity", "Semiconductors", "Telecommunications",
        "Energy and utilities", "Logistics and supply chain",
        "Sports analytics", "Real estate", "Digital health",
        "Advertising technology", "Defense",
    ],
    SUBDOMAIN_ROW: [
        "Computer vision, deep learning",
        "Natural language processing, large language models",
        "Time series forecasting, demand planning",
        "Recommender systems, personalization",
        "Causal inference, experimentation",
        "MLOps, model deployment, monitoring",
        "Data engineering, ETL pipelines",
        "Bioinformatics, genomics",
        "Reinforcement learning, robotics",
        "Statistical modeling, Bayesian methods",
        "Geospatial analysis, remote sensing",
        "Fraud detection, anomaly detection",
        "Optimization, operations research",
        "Data visualization, storytelling",
        "Graph analytics, network science",
        "Speech recognition, audio processing",
        "Survival analysis, clinical trials",
        "Information retrieval, search ranking",
        "Simulation, agent based modeling",
        "Privacy, federated learning",
        "Interpretability, model explanation",
        "Pricing, revenue management",
        "Knowledge graphs, entity resolution",
        "Edge inference, model compression",
    ],
    TOOLS_ROW: [
        "Python, PyTorch, Docker",
        "Python, scikit-learn, pandas",
        "R, tidyverse, Shiny",
        "SQL, dbt, Snowflake",
        "Spark, Databricks, Delta Lake",
        "TensorFlow, Keras, JAX",
        "Airflow, Kubernetes, Terraform",
        "Tableau, Power BI, Looker",
        "Hugging Face transformers, LangChain",
        "AWS SageMaker, Lambda, S3",
        "Git, GitHub Actions, MLflow",
        "Julia, MATLAB, NumPy",
        "Excel, Google Sheets, BigQuery",
        "Scala, Kafka, Flink",
        "Postgres, Redis, FastAPI",
        "dbt, Fivetran, Mode",
        "Ray, Weights and Biases, Optuna",
        "Streamlit, Plotly, Dash",
        "PySpark, Hive, Presto",
        "Stan, PyMC, JAGS",
        "QGIS, GeoPandas, Rasterio",
        "Snowflake, Sigma, Hex",
        "Vertex AI, BigQuery ML, Dataflow",
        "Polars, DuckDB, Parquet",
    ],
    STYLE_ROW: [
        "Direct and to the point, I say what I mean",
        "Warm and conversational, I like to build rapport first",
        "I ask a lot of questions before offering an opinion",
        "Structured, I usually come with an agenda",
        "Informal and flexible, I follow where the conversation goes",
        "I prefer written follow-ups after a call",
        "Encouraging, I lead with what is going well",
        "Analytical, I like working through problems on a whiteboard",
        "Concise in writing, more expansive in person",
        "I listen more than I talk",
        "Candid and fast-moving, I do not sugarcoat feedback",
        "Patient and detailed, I explain my reasoning",
        "I think out loud, so expect some half-formed ideas",
        "Quiet at first, but I open up quickly once I know someone",
        "I like to sleep on things before responding",
        "Playful, I use a lot of analogies",
        "Formal in writing, relaxed in conversation",
        "I check in often rather than waiting for a scheduled meeting",
        "Blunt, though I try to be kind about it",
        "I mirror whoever I am talking to",
        "Socratic, I would rather ask than tell",
        "Enthusiastic, sometimes to a fault",
    ],
    HOBBIES_ROW: [
        "Hiking, trail running, camping",
        "Playing guitar, going to concerts, collecting vinyl",
        "Cooking, baking sourdough, trying new restaurants",
        "Rock climbing, bouldering, yoga",
        "Reading science fiction, board games, puzzles",
        "Surfing, swimming, beach volleyball",
        "Photography, travel, film festivals",
        "Cycling, road trips, birdwatching",
        "Painting, ceramics, visiting museums",
        "Soccer, basketball, watching football",
        "Gardening, woodworking, home repair",
        "Chess, competitive gaming, streaming",
        "Running marathons, weightlifting, meal prep",
        "Volunteering, community organizing, tutoring",
        "Learning languages, writing, journaling",
        "Sailing, kayaking, freediving",
        "Beekeeping, foraging, making cheese",
        "Salsa dancing, live music, karaoke",
        "Restoring old cars, motorcycles, welding",
        "Knitting, quilting, embroidery",
        "Skiing, snowboarding, ice hockey",
        "Podcasting, improv comedy, community theater",
        "Fishing, hunting, cast iron cooking",
        "Astrophotography, telescope building, stargazing",
    ],
    # These two pools are deliberately as large as the others. A small pool
    # makes identical strings repeat often enough to push the upper percentile
    # to a cosine of exactly 1.0, at which point only duplicate answers score.
    LANGUAGE_ROW: [
        "I am most comfortable in Spanish and English",
        "Mandarin or English both work for me",
        "Please send materials in advance so I can read them",
        "I use captions on video calls",
        "No preferences",
        "Prefer written communication over phone calls",
        "Happy to speak Hindi or English",
        "I am hard of hearing, so video with captions works best",
        "English is my second language, please be patient with me",
        "Korean speaker, but English is fine for professional topics",
        "I need meetings scheduled outside of prayer times",
        "Screen reader user, plain text documents are easiest",
        "No accessibility needs, thanks for asking",
        "French and English both work",
        "I have ADHD and do better with shorter, more frequent check-ins",
        "Portuguese is my first language but English is fine",
        "I am dyslexic, so slides beat dense documents",
        "Low vision, large text please",
        "Arabic or English, either is comfortable",
        "I get migraines, so I avoid long video calls",
        "Vietnamese at home, English at work",
        "I stutter sometimes, please do not finish my sentences",
    ],
    ANYTHING_ELSE_ROW: [
        "I am changing careers and would value someone who did the same",
        "First generation student, guidance on navigating industry would help",
        "I work night shifts so evenings are difficult",
        "Interested in eventually starting my own company",
        "Would love a mentor who has worked abroad",
        "I am on a student visa and have questions about work authorization",
        "I am a veteran returning to school after several years",
        "Balancing a part time job with coursework, so scheduling is tight",
        "I am considering a PhD but unsure it is the right path",
        "Recently switched from a humanities background into data science",
        "I care most about finding somewhere with good work life balance",
        "Hoping to end up somewhere I can work on climate problems",
        "I am a parent, so daytime meetings work better than evenings",
        "Would prefer someone who has been through a layoff",
        "I am interested in research but have no publications yet",
        "Transfer student, so I have had less time to build a network",
        "I would like to stay in San Diego after graduating if I can",
        "Open to relocating anywhere, including outside the country",
        "I am the first in my family to finish college",
        "Recovering from burnout and rebuilding at a slower pace",
        "Neurodivergent, and direct expectations help me a lot",
        "I am an international student and new to US workplace norms",
    ],
}

SIDE_POOLS = {
    (MOTIVATION_ROW, MENTOR): [
        "I had a mentor early in my career who changed my trajectory and I want to pay that forward.",
        "I enjoy helping students figure out which parts of data science they actually like.",
        "I remember how confusing the transition from coursework to industry was.",
        "Mentoring keeps me connected to the program and to new ideas.",
        "I want to help students avoid the mistakes I made in my first few roles.",
        "I find it rewarding to watch someone grow into their first technical role.",
        "I would like to give back to HDSI, which gave me a lot.",
        "Explaining my work to students makes me better at my own job.",
        "I was a first generation student myself and had nobody to ask.",
        "My team hires a lot of new graduates and I want to understand them better.",
        "I switched fields twice and can speak to how survivable that is.",
        "I want to be the kind of manager I wish I had at twenty two.",
        "Honestly, I miss teaching, and this is the closest thing to it.",
        "I think the industry needs more people from non traditional backgrounds.",
        "I have been laid off twice and it would have helped to hear that early.",
        "I want to demystify what a data scientist actually does all day.",
    ],
    (MOTIVATION_ROW, MENTEE): [
        "I want to understand what data science work is actually like day to day.",
        "I am unsure whether to pursue graduate school or go straight to industry.",
        "I would like advice on building a portfolio that gets interviews.",
        "I want to hear from someone who moved from academia into industry.",
        "I am hoping for guidance on negotiating my first offer.",
        "I would value someone to talk through career options with.",
        "I want to learn how teams actually use machine learning in production.",
        "I would like help deciding which specialization to focus on.",
        "I have applied to sixty internships and heard nothing, and I want to know why.",
        "Nobody in my family works in tech, so I have no one to ask basic questions.",
        "I want to know whether the jobs I am aiming at will still exist in five years.",
        "I would like to meet someone who is happy in this field, honestly.",
        "I am strong technically but freeze in behavioral interviews.",
        "I want to find out whether research or industry suits me better.",
        "I am trying to decide between a startup and a large company for my first job.",
        "I would like to understand what people actually do with a masters degree.",
    ],
}

# Written so the extraction step has real terms to find, and so the hard
# constraint has genuine overlap to fire on.
AVOID_ANSWERS = [
    "I would rather not work with anyone going into finance or consulting.",
    "Prefer to avoid healthcare and pharmaceuticals.",
    "Not interested in the video games industry.",
    "I would prefer not to focus on natural language processing or large language models.",
    "Please avoid matching me with someone in investment banking.",
    "No interest in advertising technology or media.",
    "I would rather not discuss computer vision, it is not my area.",
    "Prefer someone outside of insurance.",
    "Nothing against it, but please not defense or aerospace.",
    "I would rather stay away from cryptocurrency and anything adjacent to it.",
    "Not autonomous vehicles, I have heard enough about them.",
    "Please avoid anyone whose work is mostly Tableau dashboards.",
    "I would prefer not to be matched with someone in retail or e-commerce.",
    "No reinforcement learning, robotics, or anything requiring hardware.",
    "Anything except semiconductors, I spent four years there already.",
    "Please avoid finance, consulting, and insurance if you can.",
    # Answered, but with no vocabulary term in it, so nothing is blocked.
    "Nothing comes to mind really, I am open to most things.",
    "I would rather not be matched with someone much older than me.",
]

# Answers that mean "nothing to avoid". Some are recognized outright and some
# only after the trailing punctuation is stripped.
NULL_AVOID_ANSWERS = [
    "", "", "", "", "None", "N/A", "No", "none", "nope", "-", "n/a", "nothing",
    "No preference", "Not really", "nil", "None that I can think of", ".",
]

# Free text for the "Other" option, one pool per question that offers it. Each
# pool mixes answers that clearly lean toward one listed option with ones that
# are genuinely ambiguous, so the nearest-option snap is doing real work rather
# than reading a paraphrase.
WRITE_INS = {
    COMMUNICATION_ROW: [
        "Discord", "Slack", "WhatsApp", "Signal", "Google Meet", "iMessage",
        "Voice memos", "LinkedIn messages", "Async video like Loom",
        "A shared Google Doc with comments", "A Notion page we both edit",
        "Zoom, but only if it is scheduled ahead",
        "Coffee somewhere on campus", "Walking meetings, weather permitting",
        "A standing calendar invite", "Whatever is easiest for them",
        "Anything except phone calls", "Text is fine but I am slow to reply",
        "Honestly no preference", "Carrier pigeon, if that is on offer",
    ],
    TOPICS_ROW: [
        "Negotiating a first offer",
        "Work life balance",
        "Choosing a specialization",
        "Building a portfolio",
        "Getting through the PhD application process",
        "Switching from academia to industry",
        "Imposter syndrome",
        "Finding a first internship",
        "Visa and work authorization questions",
        "Managing up",
        "Publishing without a research lab behind you",
        "Freelancing and consulting on the side",
        "Deciding whether to go to grad school at all",
        "Public speaking and conference talks",
        "Open source contributions",
        "Startup equity, and how to think about it",
        "Relocating for a job",
        "Getting promoted from analyst to scientist",
        "Building a presence online without hating it",
        "Burnout, and recovering from it",
    ],
    FEEDBACK_ROW: [
        # Leaning toward being direct and concise.
        "Blunt is fine, do not cushion it",
        "Just tell me what is wrong",
        "Short and specific, no preamble",
        "Skip the compliment sandwich please",
        "Straight to the point, I can take it",
        # Leaning toward being conversational, with examples.
        "Walk me through your reasoning with a real case",
        "I learn best from stories about what you tried",
        "Show me an example of what good looks like",
        "Talk it through with me out loud",
        "Concrete examples land better than abstract advice",
        # Leaning toward both, depending on the situation.
        "Read the room and adapt",
        "Depends entirely on the person",
        "Depends on how high the stakes are",
        "I adapt to whatever resonates",
        "Whichever the moment calls for",
        # Deliberately hard to place.
        "In writing, so I can sit with it",
        "Ask me first whether I want feedback",
        "With kindness, however it comes",
        "Over coffee rather than in a meeting",
        "Honestly I have never thought about it",
        "Both, but written down afterwards",
        "It depends on my mood that day",
    ],
}

# How often a question that offers "Other" gets a write-in instead of a listed
# option. The feedback row is highest because it is the one most worth watching
# the nearest-option snap on.
WRITE_IN_RATES = {COMMUNICATION_ROW: 0.18, TOPICS_ROW: 0.18, FEEDBACK_ROW: 0.28}

PROGRAMS = [
    "BS Data Science Major", "BS Data Science Minor", "MS in Data Science",
    "MDS (Online)", "MAS", "PhD",
]

JOB_TITLES = [
    "Data Scientist at Qualcomm", "Machine Learning Engineer at Scale AI",
    "Senior Analyst at Deloitte", "Research Scientist at Genentech",
    "Software Engineer at Google", "Product Analyst at Airbnb",
    "Quantitative Researcher at Citadel", "Data Engineer at Snowflake",
    "Applied Scientist at Amazon", "Biostatistician at UCSD Health",
    "Staff Data Scientist at Illumina", "Analytics Manager at Petco",
    "Research Engineer at Salk Institute", "Founder, early stage startup",
    "PhD student in Computer Science at UW",
]

# One long answer, used where the point is that length itself is the edge case.
LONG_ANSWER = (
    "I have been thinking about this a lot and I want to give the full picture "
    "rather than a one line answer. I came into data science from public health, "
    "where I spent three years doing survey work that was mostly cleaning other "
    "people's spreadsheets, and what pulled me across was realizing that the "
    "modeling questions I found interesting were being answered somewhere else "
    "entirely. Since then I have worked on forecasting, a little causal "
    "inference, and one project that was really just a very elaborate dashboard. "
    "What I am hoping for is someone who has been in the field long enough to "
    "tell me which of those threads is worth pulling on, and honest enough to "
    "say when the answer is none of them. I do not need a cheerleader. I would "
    "rather hear that the thing I am excited about is a dead end now than in "
    "three years, and I would rather talk to someone who has changed their mind "
    "about something important than someone who has not."
)


# --- 2. filling one cohort at random --------------------------------------

def _side_question(question: Question, side: str) -> str | None:
    return question.mentor_question if side == MENTOR else question.mentee_question


def _side_options(question: Question, side: str):
    return question.mentor_options if side == MENTOR else question.mentee_options


def _is_required(question: Question, side: str) -> bool:
    return question.mentor_required if side == MENTOR else question.mentee_required


def _listed(question: Question, side: str) -> list[str]:
    return [o.text for o in _side_options(question, side) if not o.is_write_in]


def _choose_option(rng: random.Random, question: Question, side: str) -> str:
    if rng.random() < WRITE_IN_RATES.get(question.row, 0):
        return rng.choice(WRITE_INS[question.row])
    return rng.choice(_listed(question, side))


def _choose_checkbox(rng: random.Random, question: Question, side: str) -> str:
    listed = _listed(question, side)
    # One through all of them, so both a lone selection and a full sweep turn
    # up, which is what makes zero overlap and heavy overlap both reachable.
    picked = rng.sample(listed, rng.randint(1, len(listed)))
    if rng.random() < WRITE_IN_RATES.get(question.row, 0):
        picked.append(rng.choice(WRITE_INS[question.row]))
    # Google Forms joins checkbox selections with commas.
    return ", ".join(picked)


def _answer(rng: random.Random, question: Question, side: str) -> str:
    """One respondent's answer to one question."""
    if not _is_required(question, side) and question.role != ROLE_AVOID:
        # Optional questions go unanswered often, which is what makes the
        # optional-question rule worth testing.
        if rng.random() < 0.45:
            return ""

    if question.role == ROLE_MULTIPLE_CHOICE:
        return _choose_option(rng, question, side)
    if question.role == ROLE_CHECKBOX:
        return _choose_checkbox(rng, question, side)
    if question.role == ROLE_LOCATION:
        return rng.choice(LOCATIONS)
    if question.role == ROLE_AVOID:
        if rng.random() < 0.30:
            return rng.choice(AVOID_ANSWERS)
        return rng.choice(NULL_AVOID_ANSWERS)
    if question.role == ROLE_SEMANTIC:
        pool = SIDE_POOLS.get((question.row, side)) or SHARED_POOLS.get(question.row)
        return rng.choice(pool) if pool else ""
    return ""


def _identity(rng: random.Random, row: int, side: str, name: str, email: str) -> str:
    if row == NAME_ROW:
        return name
    if row == EMAIL_ROW:
        return email
    if row == GRADUATION_ROW:
        return str(rng.randint(2020, 2026) if side == MENTOR else rng.randint(2027, 2030))
    if row == PROGRAM_ROW:
        return rng.choice(PROGRAMS)
    if row == JOB_ROW:
        return rng.choice(JOB_TITLES)
    if row == CAPACITY_ROW:
        return rng.choice(["One", "One", "Two (Each would be treated as an "
                           "individual mentee, not meeting in a group.)"])
    return ""


IDENTITY_ROWS = {NAME_ROW, EMAIL_ROW, GRADUATION_ROW, PROGRAM_ROW, JOB_ROW, CAPACITY_ROW}


def build_side(
    questions: list[Question], side: str, count: int, rng: random.Random
) -> tuple[pd.DataFrame, datetime]:
    """Fill one export at random, shaped exactly like a Google Forms sheet."""
    asked = [q for q in questions if _side_question(q, side)]
    domain = "gmail.com" if side == MENTOR else "ucsd.edu"
    submitted = datetime(2026, 7, 1, 9, 0, 0)

    records = []
    for position in range(count):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        name = f"{first} {last}"
        email = f"{first.lower()}.{last.lower()}{position}@{domain}"

        record = {"Timestamp": submitted.strftime("%-m/%-d/%Y %H:%M:%S")}
        for question in asked:
            header = _side_question(question, side)
            if question.row in IDENTITY_ROWS:
                record[header] = _identity(rng, question.row, side, name, email)
            else:
                record[header] = _answer(rng, question, side)
        records.append(record)
        submitted += timedelta(minutes=rng.randint(20, 400))

    return pd.DataFrame.from_records(records), submitted


# --- 3. the guaranteed edge cases -----------------------------------------

# Written over the random fill at fixed positions, so every case below appears
# in all four exports rather than depending on the seed. A value is either the
# replacement text, or a function of the cell it replaces when the case has to
# keep whatever was generated there.
#
# Positions run 0-15, so every side of every cohort must hold at least 16 rows.
EDGE_CASES = {
    # No name: the record falls back to the email address for display. Also a
    # zip code, which must not be read as a negative time offset.
    0: {NAME_ROW: "", LOCATION_ROW: "San Diego, CA 92093-1234"},
    # No email: the one thing flagged to a coordinator, since without an address
    # a repeat submission becomes a second person.
    1: {EMAIL_ROW: "", FEEDBACK_ROW: "Blunt is fine, do not cushion it"},
    # Neither, so the record falls back to a positional key.
    2: {NAME_ROW: "", EMAIL_ROW: "", LOCATION_ROW: "Washington, DC"},
    # An address buried in other text, which the extractor has to find.
    3: {
        EMAIL_ROW: lambda cell: f"{cell} (personal)",
        FEEDBACK_ROW: "Walk me through your reasoning with a real case",
    },
    # Stray case and padding, which normalization has to collapse before the
    # address can be used as an identity key.
    4: {EMAIL_ROW: lambda cell: f"  {cell.upper()}  ", LOCATION_ROW: "New Orleans, LA"},
    # A time difference stated outright rather than looked up.
    5: {
        FEEDBACK_ROW: "Read the room and adapt",
        LOCATION_ROW: "Remote, 4 hours ahead of Pacific",
    },
    # A checkbox write-in holding a comma, which the splitter has to cope with,
    # and a location that resolves to nothing so the question drops out.
    6: {
        COMMUNICATION_ROW: "Zoom, but only if it is scheduled ahead",
        LOCATION_ROW: "Currently travelling",
    },
    # A single checkbox selection, so a one-option overlap is reachable.
    7: {TOPICS_ROW: "Resume Review", LOCATION_ROW: ""},
    # Every optional question skipped at once.
    8: {LANGUAGE_ROW: "", ANYTHING_ELSE_ROW: "", MOTIVATION_ROW: ""},
    # An avoid answer with no vocabulary term in it, so nothing is blocked.
    9: {AVOID_ROW: "Nothing comes to mind really, I am open to most things."},
    # An avoid answer broad enough to rule out a good part of the other side.
    10: {
        AVOID_ROW: "I would rather not be matched with anyone in finance, "
                   "consulting, or insurance."
    },
    # An ambiguous write-in, next to a lone checkbox selection on another row.
    11: {
        FEEDBACK_ROW: "It depends on my mood that day",
        COMMUNICATION_ROW: "In Person",
    },
    # Every checkbox option at once, so a three-or-more overlap is guaranteed.
    12: {
        TOPICS_ROW: "Resume Review, Interview Preparation, Graduate School "
                    "Decisions, Transition to Industry, Leadership Skills, "
                    "How to Network"
    },
    # Smart quotes and a non-breaking hyphen, which only match once normalized.
    13: {
        INDUSTRY_ROW: "Ed‑tech — mostly K‑12",
        SUBDOMAIN_ROW: "“Applied” ML, broadly",
    },
    # A required multiple choice left blank anyway, which the parser has to
    # treat as unanswered rather than as an unrecognized option.
    14: {FEEDBACK_ROW: "", LOCATION_ROW: "Honolulu, HI (2 hours behind Pacific)"},
    # A two-character answer against a long one, at opposite ends of the range
    # the embedding has to place on the same scale.
    15: {INDUSTRY_ROW: "ML", ANYTHING_ELSE_ROW: LONG_ANSWER},
}

# Rows only the mentor form asks about.
MENTOR_EDGE_CASES = {
    # Blank capacity, which falls back to one mentee.
    2: {CAPACITY_ROW: ""},
    # The full option text as Google Forms exports it, which reads as two.
    5: {CAPACITY_ROW: "Two (Each would be treated as an individual mentee, "
                      "not meeting in a group.)"},
    # An unreadable capacity, which also falls back to one.
    9: {CAPACITY_ROW: "as many as you need"},
    12: {JOB_ROW: ""},
}

# One optional question nobody on a side answers, so it has no distribution to
# calibrate against and drops out of every pair in that cohort.
SILENT_ROWS = {("B", MENTEE): (ANYTHING_ELSE_ROW,)}


def _apply(frame: pd.DataFrame, headers: dict[int, str], cases: dict) -> None:
    for position, overrides in cases.items():
        for row, value in overrides.items():
            column = headers.get(row)
            if column is None:
                continue
            current = frame.at[position, column]
            frame.at[position, column] = value(current) if callable(value) else value


def _duplicate_rows(
    frame: pd.DataFrame, headers: dict[int, str], submitted: datetime
) -> pd.DataFrame:
    """The repeat submissions a real export always contains. Four rows: a later
    resubmission that must replace its original, an older one that must not, and a pair
    sharing a name with no address between them, which have to stay two people because
    there is nothing to collapse them on.
    """
    name_column, email_column = headers[NAME_ROW], headers[EMAIL_ROW]

    newer = frame.iloc[[6]].copy()
    newer["Timestamp"] = submitted.strftime("%-m/%-d/%Y %H:%M:%S")
    newer[headers[LOCATION_ROW]] = "Boulder, Colorado (1 hour ahead of Pacific)"

    # Timestamped before its original, so the original has to win.
    stale = frame.iloc[[7]].copy()
    stale["Timestamp"] = datetime(2026, 6, 28, 8, 0, 0).strftime("%-m/%-d/%Y %H:%M:%S")
    stale[headers[LOCATION_ROW]] = "Sydney, Australia"

    twins = frame.iloc[[8, 9]].copy()
    twins[name_column] = "Alex Morgan"
    twins[email_column] = ""
    twins["Timestamp"] = [
        (submitted + timedelta(minutes=offset)).strftime("%-m/%-d/%Y %H:%M:%S")
        for offset in (30, 55)
    ]

    return pd.concat([frame, newer, stale, twins], ignore_index=True)


def apply_edge_cases(
    frame: pd.DataFrame,
    questions: list[Question],
    side: str,
    tag: str,
    submitted: datetime,
) -> pd.DataFrame:
    """Write the guaranteed cases over a randomly filled export."""
    headers = {
        q.row: _side_question(q, side)
        for q in questions
        if _side_question(q, side) is not None
    }

    _apply(frame, headers, EDGE_CASES)
    if side == MENTOR:
        _apply(frame, headers, MENTOR_EDGE_CASES)

    for row in SILENT_ROWS.get((tag, side), ()):
        frame[headers[row]] = ""

    return _duplicate_rows(frame, headers, submitted)


# --- 4. writing the four exports ------------------------------------------

def output_path(tag: str, side: str) -> Path:
    return ROOT / f"Synthetic {tag} - {FORM_NAMES[side]} Questionnaire (Responses).csv"


def main() -> None:
    questions = load_questions(DATABASE)

    for cohort in COHORTS:
        tag = cohort["tag"]
        rng = random.Random(cohort["seed"])
        for side in (MENTOR, MENTEE):
            frame, submitted = build_side(questions, side, cohort[side], rng)
            frame = apply_edge_cases(frame, questions, side, tag, submitted)
            path = output_path(tag, side)
            frame.to_csv(path, index=False)
            print(f"wrote {path.name}: {len(frame)} rows x {len(frame.columns)} columns")


if __name__ == "__main__":
    main()
