"""Fixtures shared by every test file. The builders they use live in helpers.py.

Neither cohort is in version control -- the real exports because they hold real
names and addresses, the synthetic ones because the repository is public. So a
fresh clone has neither, and both fixtures below skip rather than error when
their files are absent. The tests that need no cohort at all still run.
"""

from pathlib import Path

import pytest

from app.inputs import Question, load_questions
from helpers import DATABASE, REAL_MENTEE, REAL_MENTOR, SYNTHETIC_MENTEE, SYNTHETIC_MENTOR


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
def synthetic_exports() -> tuple[Path, Path]:
    missing = [p.name for p in (SYNTHETIC_MENTOR, SYNTHETIC_MENTEE) if not p.exists()]
    if missing:
        pytest.skip(
            f"synthetic cohorts are not in version control: {', '.join(missing)}"
            " -- regenerate with: uv run python tests/fixtures/make_synthetic.py"
        )
    return SYNTHETIC_MENTOR, SYNTHETIC_MENTEE


@pytest.fixture(scope="session")
def questions() -> list[Question]:
    return load_questions(DATABASE)


@pytest.fixture(scope="session")
def by_row(questions) -> dict[int, Question]:
    return {question.row: question for question in questions}
