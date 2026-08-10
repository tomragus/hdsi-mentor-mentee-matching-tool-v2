# Test fixtures

Four cohorts, used for different things. Only the real one lives here; the
synthetic ones sit at the repository root, where they can be found and uploaded
through the app without digging through the test tree.

## `mentor_responses.csv` / `mentee_responses.csv` — the real sample

**Not in version control.** These hold real names and email addresses, so they
are gitignored and live only on local machines. Ask a coordinator for a copy.

Verbatim copies of the sample Google Forms exports from the repository root.
6 mentors and 4 mentees. This is the ground truth for anything about real
wording, real formatting quirks, and real answers.

18 of the 55 tests read them. Those take the `real_exports` fixture from
`tests/conftest.py`, which skips them when the files are absent, so a fresh
clone still runs the other 37.

Copy them in from the repository root:

    cp "../Copy of Alumni Mentor Questionnaire (Responses) - Form Responses 1.csv" mentor_responses.csv
    cp "../Copy of Student Mentee Questionnaire (Responses) - Form Responses 1.csv" mentee_responses.csv

## The two synthetic cohorts — at the repository root

    Synthetic A - Alumni Mentor Questionnaire (Responses).csv    18 mentors, 21 slots
    Synthetic A - Student Mentee Questionnaire (Responses).csv   50 mentees
    Synthetic B - Alumni Mentor Questionnaire (Responses).csv    26 mentors, 36 slots
    Synthetic B - Student Mentee Questionnaire (Responses).csv   20 mentees

Both written by `make_synthetic.py`. Upload one pair at a time — A with A, B
with B. Counts are after deduplication; the files hold a few more rows than
that, which is the point of some of them.

The real sample is too small and too tidy to reach several parts of the
pipeline: mentor slots outnumber mentees so nobody is ever waitlisted, almost
nobody answers the avoid question so the hard constraint never fires, and two
semantic questions calibrate their percentiles over as few as four pairs.

The two cohorts have deliberately opposite shapes. **A is oversubscribed**, so
roughly thirty mentees end up on the waitlist. **B is undersubscribed**, so
every mentee is placed and mentors go unused instead — the other side of the
padding in the assignment step. The tests use A, since the waitlist and
capacity assertions need mentees to outnumber slots.

Both are filled at random from the answer pools, and then a fixed table of edge
cases is written over the first sixteen rows of each file so the awkward inputs
are guaranteed rather than left to the seed. Between them they cover: a missing
name, a missing email, a row missing both, an address buried in other text, a
resubmission that must replace its original, an older resubmission that must
not, two people sharing a name with no address to tell them apart, blank and
unreadable mentor capacities, every branch of the time zone parser including
answers that resolve to nothing, "Other" write-ins on all three questions that
offer one, a checkbox write-in containing a comma, single and full checkbox
selections, smart quotes and a non-breaking hyphen, a two-character answer
against a several-hundred-word one, and every optional question skipped at
once. Cohort B additionally has no mentee answering the last optional question
at all, so that row has no distribution to calibrate against and drops out of
every pair.

**Neither is an evaluation set.** The free text is sampled from curated pools,
so it says nothing about whether a real match would be a good one. Judge match
quality on the real sample.

Regenerate (deterministic, one seed per cohort, writes straight to the
repository root):

    uv run python tests/fixtures/make_synthetic.py

Column headers are read from the questions database rather than hard-coded, so
editing a question in the CSV flows into both cohorts on the next regeneration
instead of silently breaking the link between them.

## The original synthetic pair — also at the repository root

    Synthetic Alumni Mentor Questionnaire (Responses).csv    15 mentors, 23 slots
    Synthetic Student Mentee Questionnaire (Responses).csv   40 mentees

Kept for comparison, and still uploadable. **The generator no longer produces
these** — it writes the A and B pair above instead, so running it will not
recreate or update them. Treat them as a frozen snapshot of the older, smaller
cohort rather than something to regenerate.
