"""Shared test setup.

The sample questionnaire exports hold real names and email addresses, so they
are kept out of version control. Tests that read them ask for the
`real_exports` fixture, which skips them when the files are absent. Everything
that runs on the synthetic cohort still runs on a fresh clone.
"""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

REAL_EXPORTS = (
    FIXTURES / "mentor_responses.csv",
    FIXTURES / "mentee_responses.csv",
)


@pytest.fixture(scope="session")
def real_exports() -> tuple[Path, ...]:
    missing = [path.name for path in REAL_EXPORTS if not path.exists()]
    if missing:
        pytest.skip(
            f"real questionnaire exports are not in version control: {', '.join(missing)}"
            " -- see tests/fixtures/README.md"
        )
    return REAL_EXPORTS
