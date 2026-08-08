"""Generate a synthetic cohort large enough to exercise the whole pipeline.

The real sample exports are only 6 mentors and 4 mentees, which leaves several
code paths unreachable: mentor slots outstrip mentees so nobody is waitlisted,
almost nobody answers the avoid question so the hard constraint never fires,
and percentile calibration runs over as few as four pairs.

This writes a bigger cohort with those situations built in. It is a test
fixture, not an evaluation set -- the free text is drawn from curated pools and
says nothing about whether a real match would be any good.

Column headers come from the questions database itself, so a change there flows
into the fixture rather than silently breaking the link between them.

Writes to the repository root, next to the real exports. Regenerate with:

    uv run python tests/fixtures/make_synthetic.py
"""

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import pandas as pd

from app.questions import (
    ROLE_AVOID,
    ROLE_CHECKBOX,
    ROLE_LOCATION,
    ROLE_MULTIPLE_CHOICE,
    ROLE_SEMANTIC,
    Question,
    load_questions,
)
from app.respondents import MENTEE, MENTOR

SEED = 4242
MENTOR_COUNT = 15
MENTEE_COUNT = 40

ROOT = Path(__file__).parents[3]
DATABASE = ROOT / "Mentee_Mentor Questions Database.csv"

# Written to the repository root, alongside the real exports, so they are easy
# to find and upload through the app rather than buried under the test tree.
OUTPUT = {
    MENTOR: ROOT / "Synthetic Alumni Mentor Questionnaire (Responses).csv",
    MENTEE: ROOT / "Synthetic Student Mentee Questionnaire (Responses).csv",
}

NAME_ROW, EMAIL_ROW, GRADUATION_ROW = 19, 20, 21
PROGRAM_ROW, JOB_ROW, CAPACITY_ROW = 22, 23, 24
INDUSTRY_ROW, LOCATION_ROW, MOTIVATION_ROW = 6, 7, 8
AVOID_ROW, SUBDOMAIN_ROW, TOOLS_ROW = 12, 13, 14
STYLE_ROW, HOBBIES_ROW = 15, 16
LANGUAGE_ROW, ANYTHING_ELSE_ROW = 17, 18

FIRST_NAMES = [
    "Amara", "Ben", "Chloe", "Diego", "Elena", "Farid", "Grace", "Hana",
    "Ibrahim", "Jonas", "Kavya", "Liam", "Mei", "Nadia", "Omar", "Priya",
    "Quinn", "Rafael", "Sofia", "Tomas", "Uma", "Viktor", "Wren", "Xiulan",
    "Yusuf", "Zara", "Aditi", "Bruno", "Cara", "Dmitri", "Esi", "Freya",
    "Gustavo", "Hiro", "Ines", "Jamal", "Kiran", "Lucia", "Mateo", "Noor",
    "Oscar", "Paloma", "Ravi", "Sena", "Tariq", "Ursula", "Vikram", "Wei",
    "Yara", "Zoltan", "Anika", "Bilal", "Clara", "Devon", "Emeka",
]

LAST_NAMES = [
    "Okafor", "Nakamura", "Silva", "Ferreira", "Kowalski", "Haddad", "Osei",
    "Lindqvist", "Rahman", "Petrov", "Iyer", "Murphy", "Zhang", "Karimi",
    "Diallo", "Sharma", "OBrien", "Castillo", "Rossi", "Novak", "Bauer",
    "Tanaka", "Mensah", "Reyes", "Andersen", "Volkov", "Chowdhury", "Marino",
    "Dubois", "Fitzgerald", "Nguyen", "Adeyemi", "Salazar", "Bergstrom",
    "Alkhatib", "Popescu", "Yilmaz", "Choudhury", "Barros", "Lindgren",
]

# Locations chosen to spread across the point bands: same hour as Pacific,
# one to two hours away, and far enough to score nothing.
LOCATIONS = [
    "San Diego, California, USA", "Los Angeles, California, USA",
    "San Francisco, California, USA", "Seattle, Washington, USA",
    "Portland, Oregon, USA", "Irvine, California, USA",
    "Denver, Colorado, USA", "Phoenix, Arizona, USA",
    "Salt Lake City, Utah, USA", "Austin, Texas, USA",
    "Chicago, Illinois, USA", "Minneapolis, Minnesota, USA",
    "New York, New York, USA", "Boston, Massachusetts, USA",
    "Atlanta, Georgia, USA", "Toronto, Ontario, Canada",
    "Mexico City, Mexico", "London, United Kingdom", "Berlin, Germany",
    "Bangalore, India", "Singapore", "Tokyo, Japan", "Sydney, Australia",
    "Sao Paulo, Brazil",
    # Stated hour differences, which the scorer should read directly.
    "Remote, 3 hours ahead of Pacific Time",
    "Boulder, Colorado (1 hour ahead of Pacific)",
    # Unresolvable, and so a candidate for admin review.
    "Currently travelling",
    "Prefer not to say",
]

SHARED_POOLS = {
    INDUSTRY_ROW: [
        "Technology", "Software", "Finance", "Investment banking",
        "Management consulting", "Healthcare", "Biotechnology",
        "Pharmaceuticals", "Video games", "Climate and sustainability",
        "Education technology", "Government and public policy", "Retail",
        "E-commerce", "Aerospace", "Media and entertainment", "Nonprofit",
        "Insurance", "Agriculture technology", "Autonomous vehicles",
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
    "No interest in advertising or media.",
    "I would rather not discuss computer vision, it is not my area.",
    "Prefer someone outside of insurance.",
]

NULL_AVOID_ANSWERS = ["", "", "", "", "None", "N/A", "No", "none", "nope", "-"]

WRITE_INS = {
    2: ["Discord", "Whatever works best", "Anything but email", "Slack"],
    3: [
        "Negotiating a first offer",
        "Work life balance",
        "Choosing a specialization",
        "Building a portfolio",
    ],
    9: [
        "Depends entirely on the person",
        "I adapt to whatever resonates",
    ],
}

PROGRAMS = [
    "B.S. Data Science", "M.S. Data Science", "Ph.D. Data Science",
    "B.S. Mathematics-Computer Science", "Data Science Minor",
]

JOB_TITLES = [
    "Data Scientist at Qualcomm", "Machine Learning Engineer at Scale AI",
    "Senior Analyst at Deloitte", "Research Scientist at Genentech",
    "Software Engineer at Google", "Product Analyst at Airbnb",
    "Quantitative Researcher at Citadel", "Data Engineer at Snowflake",
    "Applied Scientist at Amazon", "Biostatistician at UCSD Health",
]


def _side_question(question: Question, side: str) -> str | None:
    return question.mentor_question if side == MENTOR else question.mentee_question


def _side_options(question: Question, side: str):
    return question.mentor_options if side == MENTOR else question.mentee_options


def _is_required(question: Question, side: str) -> bool:
    return question.mentor_required if side == MENTOR else question.mentee_required


def _choose_option(rng: random.Random, question: Question, side: str) -> str:
    listed = [o.text for o in _side_options(question, side) if not o.is_write_in]
    if question.row in WRITE_INS and rng.random() < 0.08:
        return rng.choice(WRITE_INS[question.row])
    return rng.choice(listed)


def _choose_checkbox(rng: random.Random, question: Question, side: str) -> str:
    listed = [o.text for o in _side_options(question, side) if not o.is_write_in]
    picked = rng.sample(listed, rng.randint(2, min(5, len(listed))))
    if question.row in WRITE_INS and rng.random() < 0.10:
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
        return str(rng.randint(2016, 2024) if side == MENTOR else rng.randint(2027, 2030))
    if row == PROGRAM_ROW:
        return rng.choice(PROGRAMS)
    if row == JOB_ROW:
        return rng.choice(JOB_TITLES)
    if row == CAPACITY_ROW:
        # Roughly 22 slots across 15 mentors, against 40 mentees.
        return rng.choice(["1. One", "1. One", "2. Two"])
    return ""


def build_side(
    questions: list[Question], side: str, count: int, rng: random.Random
) -> pd.DataFrame:
    """Build one export, shaped exactly like a Google Forms response sheet."""
    identity_rows = {NAME_ROW, EMAIL_ROW, GRADUATION_ROW, PROGRAM_ROW, JOB_ROW, CAPACITY_ROW}
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
            if question.row in identity_rows:
                record[header] = _identity(rng, question.row, side, name, email)
            else:
                record[header] = _answer(rng, question, side)
        records.append(record)
        submitted += timedelta(minutes=rng.randint(20, 400))

    frame = pd.DataFrame.from_records(records)
    return _inject_edge_cases(frame, side, questions, submitted)


def _inject_edge_cases(
    frame: pd.DataFrame, side: str, questions: list[Question], submitted: datetime
) -> pd.DataFrame:
    """Add the messy submissions a real export always contains."""
    by_row = {q.row: q for q in questions}
    name_column = _side_question(by_row[NAME_ROW], side)
    email_column = _side_question(by_row[EMAIL_ROW], side)

    # Someone who skipped the name field, and someone who skipped their email.
    frame.loc[3, name_column] = ""
    frame.loc[7, email_column] = ""

    # A resubmission, which must replace the original rather than add a person.
    resubmission = frame.iloc[[1]].copy()
    resubmission["Timestamp"] = submitted.strftime("%-m/%-d/%Y %H:%M:%S")
    if side == MENTOR:
        resubmission[_side_question(by_row[CAPACITY_ROW], side)] = "2. Two"
    return pd.concat([frame, resubmission], ignore_index=True)


def main() -> None:
    rng = random.Random(SEED)
    questions = load_questions(DATABASE)

    for side, count in ((MENTOR, MENTOR_COUNT), (MENTEE, MENTEE_COUNT)):
        frame = build_side(questions, side, count, rng)
        path = OUTPUT[side]
        frame.to_csv(path, index=False)
        print(f"wrote {path.name}: {len(frame)} rows x {len(frame.columns)} columns")


if __name__ == "__main__":
    main()
