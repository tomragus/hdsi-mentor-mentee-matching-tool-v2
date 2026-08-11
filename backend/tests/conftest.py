"""Fixtures shared by every test file. The builders they use live in helpers.py.

The sample questionnaire exports hold real names and email addresses, so they
are kept out of version control. Tests that read them ask for the
`real_exports` fixture, which skips them when the files are absent. Everything
that runs on the synthetic cohorts still runs on a fresh clone.
"""

from pathlib import Path

import pytest

from app.inputs import Question, load_questions
from helpers import DATABASE, REAL_MENTEE, REAL_MENTOR


@pytest.fixture(scope="session")
def real_exports() -> tuple[Path, Path]:
    missing = [path.name for path in (REAL_MENTOR, REAL_MENTEE) if not path.exists()]
    if missing:
        pytest.skip(
            f"real questionnaire exports are not in version control: {', '.join(missing)}"
            " -- see tests/fixtures/README.md"
        )
    return REAL_MENTOR, REAL_MENTEE


@pytest.fixture(scope="session")
def questions() -> list[Question]:
    return load_questions(DATABASE)


@pytest.fixture(scope="session")
def by_row(questions) -> dict[int, Question]:
    return {question.row: question for question in questions}
