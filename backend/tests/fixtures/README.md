# Test fixtures

Two cohorts, used for different things. Only the real one lives here; the
synthetic one sits at the repository root, where it can be found and uploaded
through the app without digging through the test tree.

## `mentor_responses.csv` / `mentee_responses.csv` — the real sample

**Not in version control.** These hold real names and email addresses, so they
are gitignored and live only on local machines. Ask a coordinator for a copy.

Verbatim copies of the sample Google Forms exports from the repository root.
6 mentors and 4 mentees. This is the ground truth for anything about real
wording, real formatting quirks, and real answers.

Around 60 tests read them. Those tests take the `real_exports` fixture from
`tests/conftest.py`, which skips them when the files are absent, so a fresh
clone still runs the other 110.

Copy them in from the repository root:

    cp "../Copy of Alumni Mentor Questionnaire (Responses) - Form Responses 1.csv" mentor_responses.csv
    cp "../Copy of Student Mentee Questionnaire (Responses) - Form Responses 1.csv" mentee_responses.csv

## The synthetic cohort — at the repository root

    Synthetic Alumni Mentor Questionnaire (Responses).csv
    Synthetic Student Mentee Questionnaire (Responses).csv

15 mentors (23 mentee slots) and 40 mentees, written by `make_synthetic.py`.

The real sample is too small and too tidy to reach several parts of the
pipeline: mentor slots outnumber mentees so nobody is ever waitlisted, almost
nobody answers the avoid question so the hard constraint never fires, and two
semantic questions calibrate their percentiles over as few as four pairs.

The synthetic cohort is built to reach them. It is deliberately oversubscribed,
about a quarter of respondents answer the avoid question with something
extractable, locations span several time zones including two that cannot be
resolved, and it contains a resubmission, a missing email, and a missing name
on each side.

**It is not an evaluation set.** The free text is sampled from curated pools, so
it says nothing about whether a real match would be a good one. Judge match
quality on the real sample.

Regenerate (deterministic, seeded, writes straight to the repository root):

    uv run python tests/fixtures/make_synthetic.py

Column headers are read from the questions database rather than hard-coded, so
editing a question in the CSV flows into the cohort on the next regeneration
instead of silently breaking the link between them.
