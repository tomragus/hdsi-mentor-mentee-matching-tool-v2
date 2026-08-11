# Architecture

A guide to this repository for someone reading it for the first time.

This is about **how the code is built**, not what the product does. Where the
matching rules come up, they come up because they explain a structural decision —
why a boundary sits where it does, why a value is `None` instead of `0`, why one
function got a name and another didn't.

Every claim below cites `file:line` so you can check it. Line numbers refer to
the commit this document was written against; if one is off by a few, the
function name will still find it.

---

## Contents

1. [Orientation](#1-orientation)
2. [Running it](#2-running-it)
3. [One upload, end to end](#3-one-upload-end-to-end)
4. [Reference, file by file](#4-reference-file-by-file)
5. [Conventions to keep](#5-conventions-to-keep)
6. [Sharp edges](#6-sharp-edges)

---

## 1. Orientation

Two halves that talk over HTTP and share nothing else.

```
backend/app/            frontend/src/
  config.py               main.tsx
  inputs.py               App.tsx
  matching.py             index.css
  main.py
```

Seven source files, 2,982 lines of application code. That is small enough that
the organising principle is **fewer, larger files grouped by role** rather than
one file per concept. Following an answer from a CSV cell to a percentage on
screen means reading four files in order, not fifteen.

| File | Lines | Job |
|---|---|---|
| [`backend/app/config.py`](backend/app/config.py) | 95 | Every tunable constant, plus the one string-normalising function |
| [`backend/app/inputs.py`](backend/app/inputs.py) | 768 | Files → structured, comparable answers |
| [`backend/app/matching.py`](backend/app/matching.py) | 782 | Answers → scores → an assignment |
| [`backend/app/main.py`](backend/app/main.py) | 285 | The five HTTP endpoints and the JSON they return |
| [`frontend/src/App.tsx`](frontend/src/App.tsx) | 667 | The entire client: types, fetch layer, every component |
| [`frontend/src/index.css`](frontend/src/index.css) | 372 | All styling, hand-written, no framework |
| [`frontend/src/main.tsx`](frontend/src/main.tsx) | 13 | Mounts `App` into the DOM |
| [`backend/tests/`](backend/tests/) | 1,139 | 66 tests across four files, plus `conftest.py` and `helpers.py` |
| [`backend/tests/fixtures/make_synthetic.py`](backend/tests/fixtures/make_synthetic.py) | 756 | Generates the two synthetic cohorts |

### The layering

The single most useful thing to know about the backend:

```
config.py  ←  inputs.py  ←  matching.py  ←  main.py
```

**No module imports anything to its right.** This is a strict, acyclic layering,
and it is what makes the codebase navigable — you can read `config.py` knowing
nothing, then `inputs.py` knowing only `config.py`, and so on.

One nuance: `main.py` imports from *both* `inputs.py` and `matching.py`
([`main.py:22-30`](backend/app/main.py#L22-L30)), not just the layer directly
beneath it. That is deliberate — the HTTP layer needs to read files
(`read_export`, `load_questions`) *and* run the solve (`prepare`, `solve`), so it
talks to two stages. It is still a strict layering; it just isn't a chain.

### The convention that tells you a file's shape

`inputs.py` and `matching.py` both open with a docstring that **enumerates
numbered stages**, then use banner comments that map onto those stages one to
one:

```python
# --- 1. the questions database --------------------------------------------
# --- 2. the form exports --------------------------------------------------
# --- 3. parsing answers ---------------------------------------------------
# --- 4. embedding and write-ins -------------------------------------------
```

Read the docstring first and you know the file's layout before scrolling. Both
files list four stages and carry four banners; `matching.py` splits its into
`1a`/`1b`/`1c` for the three kinds of question scorer.

---

## 2. Running it

**Backend** (from `backend/`):

```bash
uv run uvicorn app.main:app        # serves on 127.0.0.1:8000
uv run pytest -q                   # 66 tests
```

**Frontend** (from `frontend/`):

```bash
npm run dev                        # Vite dev server on :5173
npm run build                      # tsc as a type-check gate, then vite build
npm run lint                       # oxlint, not ESLint
```

Note `build` is `tsc && vite build` ([`package.json`](frontend/package.json)) —
TypeScript never emits anything (`"noEmit": true`), it runs purely as a gate
before Vite/esbuild does the real bundling.

### The proxy

[`vite.config.ts`](frontend/vite.config.ts) forwards `/api` to
`http://127.0.0.1:8000`. This is why every fetch in the client uses a bare
relative path (`/api/run`, `/api/upload`) with no base URL anywhere. It also
means a client running against the dev server and a client served from a built
bundle behave identically as long as something answers `/api`.

When the backend isn't listening, the dev server answers with a gateway error and
no JSON body. `send` detects exactly that case and returns a message naming the
command to fix it ([`App.tsx:52`](frontend/src/App.tsx#L52)).

### One gotcha worth stating loudly

**Backend edits need a uvicorn restart.** There is no reload in the documented
command. A stale server on `:8000` serving code you have already changed has
produced confusing, wrong results more than once in this project's history. If a
backend change appears to have done nothing, check that first.

---

## 3. One upload, end to end

This is the section to read if you only read one. Two CSVs go in; a table of
pairs comes out. Here is every hop.

### Stage 1 — the questions database becomes configuration

Before any response is read, [`load_questions`](backend/app/inputs.py#L321)
parses [`Mentee_Mentor Questions Database.csv`](Mentee_Mentor%20Questions%20Database.csv)
into a list of `Question` records.

That CSV is the application's real configuration file. Its columns are:

```
Question Response Type, Question Required?, Mentor Question,
Mentor Response Options, Mentee Question, Mentee Response Options,
Response Matching Criteria (any order), Weight, Similarity Percentile Cutoffs
```

Each row declares one question: its input type, its wording *on each side
separately*, its option list on each side, how to score a pair of answers, its
weight, and (for free-text rows) the percentile cutoffs. Adding a question to the
forms means adding a row here — no Python changes.

`load_questions` assigns each row a `role` via [`_route`](backend/app/inputs.py#L296),
which is the fork that decides everything downstream: `multiple_choice`,
`checkbox`, `semantic`, `location`, `avoid`, or `unscored`.

### Stage 2 — the exports are read and linked

[`read_export`](backend/app/inputs.py#L384) reads a CSV or XLSX with `dtype=str` —
everything stays a string. This matters: a graduation year read as an integer
becomes `2027` and then `"2027"` again with different formatting, and
`test_reads_values_as_text` pins it.

Which reader runs is chosen by **the filename extension, not the contents**, so a
file can easily reach the wrong one. Anything unreadable becomes an
[`ExportReadError`](backend/app/inputs.py#L130) rather than whatever pandas threw,
tagged with one of two kinds: `READ_WRONG_TYPE` for a file that is not the format
its name claims, and `READ_MALFORMED` for text that has a header but whose rows do
not line up with it. The endpoint turns each into different advice, because the
first needs a different file and the second needs the sheet tidying up. Both are a
400 — an unreadable upload is the uploader's problem, not the server's.

[`link_columns`](backend/app/inputs.py#L419) then pairs each database row to a
column in each export **by matching question text, not by column position**.

This is the first real design decision worth pausing on. Google Forms exports
columns in whatever order the form is currently in. Keying on position would mean
that reordering questions silently rescores everyone.
`test_linking_ignores_column_order` shuffles the columns and asserts the mapping
is unchanged.

When a question can't be found, `link_columns` raises
[`ExportLinkError`](backend/app/inputs.py#L115) carrying **every** unresolved
question, not just the first. That list survives all the way to the browser — it
is the reason the error type exists at all, and the reason `Result<T>` on the
frontend has a `missing?` field.

**Before trusting that error, `upload` tries the two frames the other way round.**
The two forms word most of their questions differently, so a pair that will not
link one way and links cleanly the other way is the same two files in the wrong
two boxes — the likeliest mistake a coordinator can make, and one that otherwise
answers with 37 missing questions instead of one sentence. The linker is the
judge, so this is a proof rather than a guess: if the swapped order links, the run
goes ahead swapped and the response carries `{"swapped": true}` for the client to
mention ([`main.py:174-198`](backend/app/main.py#L174-L198)). If neither order
links, the files really are mismatched and the original error is raised untouched.

### Stage 3 — rows become people

[`build_respondents`](backend/app/inputs.py#L506) turns a dataframe into
`Respondent` records, keyed by email address, collapsing resubmissions so the
latest wins ([`_is_newer`](backend/app/inputs.py#L496)).

Email is the identity key, which is why a respondent without a readable one is the
single thing flagged for coordinator review
([`missing_email`](backend/app/inputs.py#L463)) — not because the address is
needed, but because duplicate submissions from that person can't be detected.

### Stage 4 — answers become option indices

[`parse_responses`](backend/app/inputs.py#L625) resolves each answer cell against
its question's option list, once. After this point **everything downstream
compares integers, not text.**

That single decision is what lets the two forms word the same option differently.
Row 9 asks about feedback preferences with different phrasing on each side;
because each side is matched against its own option list, the same underlying
choice lands on the same index either way, and
`test_differently_worded_options_align_by_index` proves it.

Anything matching no listed option is carried forward as a **write-in**, kept as
raw text on the `Response`.

### Stage 5 — one embedding pass

[`build_cache`](backend/app/inputs.py#L691) gathers every distinct string that
will need a vector ([`collect_texts`](backend/app/inputs.py#L649)) and embeds them
all in one batch ([`embed`](backend/app/inputs.py#L680)).

The vectors are unit length, so cosine similarity reduces to a dot product
([`similarity`](backend/app/inputs.py#L698)). Embedding once and reusing is the
difference between one model pass and one per pair.

`similarity` raises `KeyError` on a cache miss rather than recomputing. That is
intentional: a miss means `collect_texts` has a bug, and silently papering over it
would make the bug invisible. `test_uncollected_string_raises` locks that in.

Then [`resolve_write_ins`](backend/app/inputs.py#L751) snaps each write-in to the
listed option it most resembles ([`nearest_option`](backend/app/inputs.py#L712)),
while keeping the original text — its presence is what triggers the write-in
penalty later ([`penalty`](backend/app/inputs.py#L763)).

### Stage 6 — cohort-wide calibration

[`calibrate`](backend/app/matching.py#L113) derives similarity cutoffs for each
free-text question **from the cohort's own distribution** of scores.

A fixed cutoff can't work here: what counts as a "similar" answer depends entirely
on what people wrote this cycle. So each semantic question gets its own `Cutoffs`
from percentiles of the actual pairwise similarities.

[`resolve_offsets`](backend/app/matching.py#L317) does the equivalent for
location, turning free-text places into hours-from-Pacific.

All of this lands in a [`ScoringContext`](backend/app/matching.py#L368) — the
values that depend on *who submitted*, computed once and threaded explicitly
through scoring rather than stashed in a global.

[`prepare`](backend/app/matching.py#L490) runs stages 3–6 in order and hands back
`(mentors, mentees, context)`.

### Stage 7 — every pair is scored

[`score_all`](backend/app/matching.py#L477) scores the **full** mentor × mentee
matrix. Not the pairs that look promising — all of them.

[`score_pair`](backend/app/matching.py#L436) sums each scored question's points ×
weight, subtracts write-in penalties, and divides by the maximum achievable **on
the questions both parties actually answered**.

Two things to internalise:

**`None` is not `0`.** Each scorer returns 10, 5, 0, or `None`. `None` means the
question can't be scored for this pair, and drops out of *both* the numerator and
the denominator. So skipping an optional question costs nothing, while disagreeing
costs real points. Two tests sit directly on this distinction:
`test_a_skipped_question_leaves_the_ratio_untouched` and
`test_a_disagreement_is_not_the_same_as_a_skip` in
[`test_scoring.py`](backend/tests/test_scoring.py).

**The ratio ranks, not the raw total.** Otherwise a pair would rank higher merely
for having had more questions in common to earn points on.

### Stage 8 — the avoid constraint

[`build_vocabulary`](backend/app/matching.py#L573) builds a closed vocabulary from
the surveys themselves, then
[`extract_avoid_terms`](backend/app/matching.py#L605) resolves each person's "what
would you rather avoid" answer against it, once — not per pair.

[`blocked_cells`](backend/app/matching.py#L651) produces the set of
`(mentor_key, mentee_key)` pairs where one side works on what the other asked to
avoid. Matching is **exact, not partial**: "investment banking" does not match
"banking", per `test_matching_is_exact_not_partial`.

### Stage 9 — the global solve

[`build_slots`](backend/app/matching.py#L701) expands mentors into one entry per
opening. [`build_matrix`](backend/app/matching.py#L717) builds the padded score
matrix. [`solve`](backend/app/matching.py#L744) runs the Hungarian algorithm via
`scipy.optimize.linear_sum_assignment`.

**Global, not greedy.** Taking the best pair, then the next best, looks reasonable
and isn't: an early pair claims a mentor a later mentee needed far more, and the
cohort ends up worse overall. `test_the_global_solve_beats_picking_greedily`
constructs exactly that situation with hand-built scores.

Blocked pairs get [`BLOCKED_SCORE`](backend/app/matching.py#L678) (`-1.0e6`)
rather than being removed from the matrix. A finite penalty keeps the problem
solvable — a mentee blocked from everyone lands on the waitlist instead of making
the solve infeasible
(`test_a_fully_blocked_mentee_is_waitlisted_rather_than_forced`).

Ties get deterministic jitter of
[`TIE_BREAK_RANGE`](backend/app/matching.py#L681) (`1.0e-9`) seeded from
`RANDOM_SEED`, so identical inputs give identical output across runs.

### Stage 10 — JSON, and the client

[`build_report`](backend/app/main.py#L82) assembles the response: `matches`,
`waitlist`, `unmatched_mentors`, `review_flags`.

The client's [`send<T>`](frontend/src/App.tsx#L54) receives it and returns a
`Result<T>`. `App` stores it in one `report` state
([`App.tsx:126`](frontend/src/App.tsx#L126)), and `Results` **derives everything
else from it on every render** — the live match list, mentor usage counts, and
both manual-review pools
([`App.tsx:408-442`](frontend/src/App.tsx#L408-L442)).

### Where a manual match gets its score

Because stage 7 scored *every* pair, a pair the solver never chose already has a
real score sitting in the session. When the coordinator drags a mentee onto a
mentor, `handlePair` ([`App.tsx:218`](frontend/src/App.tsx#L218)) calls
`GET /api/match/{mentor}/{mentee}`, and the endpoint does a **dict lookup**:

```python
_session["scores"].get((mentor_key, mentee_key))
```

No recomputation, no second code path. Same `score_pair`, same weights, same
cohort-wide calibration — which is also why solver and manual percentages are
directly comparable and can be sorted into one table.

---

## 4. Reference, file by file

### `config.py` — 95 lines

Constants and [`normalize`](backend/app/config.py#L83). Everything is a constant
rather than a runtime setting, so a run is reproducible from its inputs alone.

`normalize` exists because the questions database and the form exports differ in
invisible ways — the database contains non-breaking hyphens, exports carry smart
quotes and trailing spaces. It applies NFKC, casefolds, collapses whitespace, and
maps three families of Unicode punctuation to ASCII
([`_ASCII_EQUIVALENTS`](backend/app/config.py#L74)). Original text is always kept
for display; only comparisons use the normalized form. Anything that isn't a
string — including the `NaN` pandas puts in empty cells — normalizes to `""`,
which is what makes [`is_blank`](backend/app/config.py#L93) a one-liner.

[`DISPLAY_ORDER`](backend/app/config.py#L45) is reading order for a match or a
person, as database row numbers. Display only — nothing about scoring reads it.

### `inputs.py` — 768 lines

Four stages, in the order they run, matching the four banner comments.

**1. The questions database.** The tricky part is
[`_parse_choice_scores`](backend/app/inputs.py#L263), which reads a criteria cell
like `{10: Yes & Yes, No & No; 5: Maybe & Yes, ...}` into a
`(mentor_index, mentee_index) -> points` table. Options are split on semicolons
only, never commas
([`_parse_options`](backend/app/inputs.py#L183)), because at least one option's
own text contains a comma. Combinations count in either order, so each stated pair
contributes both orderings, and higher point buckets are processed first so they
win ties. [`_split_shared_chunk`](backend/app/inputs.py#L224) handles the one
genuinely nasty case: `"...Yes, Maybe & No..."` has to split at the comma where
*both* halves resolve to real options.

**2. The form exports.** `read_export`, `link_columns`, `build_respondents`, as
described in stages 2–3 above.

**3. Parsing answers.** [`parse_response`](backend/app/inputs.py#L597) branches on
role. [`_split_checkbox`](backend/app/inputs.py#L576) is the subtle one: Google
Forms joins checkbox selections with commas while the database separates options
with semicolons, so the cell is split on commas and adjacent pieces re-joined
whenever the longer run is itself a listed option — otherwise
`"Both, depending on the day"` becomes two bogus write-ins.

**4. Embedding and write-ins.** `collect_texts` → `embed` → `resolve_write_ins`.
[`load_model`](backend/app/inputs.py#L640) imports `sentence_transformers` inside
the function rather than at module level, so the API server does not pay several
seconds of torch import just to serve an upload page.

### `matching.py` — 782 lines

**1a–1c. The scorers.** `score_multiple_choice` and `score_checkbox` work on
indices. `score_semantic` compares against cohort-derived `Cutoffs`. Location is
its own thing: [`_ZONE_NAMES`](backend/app/matching.py#L168) and
[`_ZONE_CODES`](backend/app/matching.py#L224) are comma-separated strings split
into lookup dicts at import. Two-letter codes are matched **only as a whole
segment** — as substrings they would be a disaster, since "LA" is Louisiana in
"New Orleans, LA" while "IN" and "OR" are ordinary English words. A respondent's
own stated offset wins over the table
([`_stated_offset`](backend/app/matching.py#L270)), and
[`_SIGNED`](backend/app/matching.py#L249) is capped at two digits so the `-1234`
of a zip code is not read as an offset.

**2. Pair scoring.** `_points_for` routes; `score_pair` accumulates. The write-in
penalty is subtracted *after* the weight multiplier, so it costs the same on a
weight-3 question as on a weight-1 one.

**3. The avoid constraint.** A closed vocabulary keeps this from firing on loose
similarity. [`JUNK_TERMS`](backend/app/matching.py#L535) and the length floor drop
terms like "R" and "AI" that would otherwise match nearly everyone and block a
person against the whole cohort.

**4. The assignment.** `build_slots`, `build_matrix`, `solve`.

### `main.py` — 285 lines

Five endpoints: `POST /api/upload`, `POST /api/run`,
`GET /api/match/{mentor}/{mentee}`, `GET /api/person/{key}`, `GET /api/health`.

Response shapes are built as dicts directly rather than modelled as dataclasses
and copied field by field — there is one caller and one consumer.
[`_session`](backend/app/main.py#L45) is a module-level dict holding the uploaded
cohort for the life of the process; see [Sharp edges](#6-sharp-edges).

### `App.tsx` — 667 lines

The whole client, in four parts: the response types and `send<T>` fetch layer, the
`App` shell holding all state, the `Upload` and `Results` components, and the two
overlay sheets.

`send` never throws — it returns a `Result<T>` discriminated union, so the type
system forces callers to handle failure before reading data, and the upload error
can carry its `missing` list.

`App` holds all state. Only two pieces are manual: `pulled` (solver matches broken
apart) and `manualPairs` (pairs made by hand). Undo keeps whole snapshots of those
two rather than a list of inverse actions.

`Results` derives the rest each render. [`Who`](frontend/src/App.tsx#L370) renders
name, capacity tag and review flag in one place, so the matches table and both
pool columns cannot drift apart.

### `index.css` — 372 lines

Hand-written, no framework, light-only by design. The drag-and-drop cluster
carries the highest-value comments in the file — the tooltip is `display: none`
rather than merely invisible because a hidden box still counts towards what a card
overflows, which is the region the browser photographs for the drag image, so an
invisible tooltip used to drag the card below along with it.

### Tests — 1,139 lines, 66 tests

| File | Lines | Covers |
|---|---|---|
| [`conftest.py`](backend/tests/conftest.py) | 35 | Fixtures only: `real_exports`, `questions`, `by_row` |
| [`helpers.py`](backend/tests/helpers.py) | 168 | Paths, and the stand-in record builders the test files share |
| [`test_inputs.py`](backend/tests/test_inputs.py) | 259 | Reading exports, linking, dedup, parsing, embedding |
| [`test_scoring.py`](backend/tests/test_scoring.py) | 238 | The five scorers and pair assembly |
| [`test_matching.py`](backend/tests/test_matching.py) | 266 | Avoid constraint, the solve, the report |
| [`test_api.py`](backend/tests/test_api.py) | 173 | The HTTP surface |

Fixtures and builders are centralised so the four test files do not each define
their own `Question` factory: [`stand_in`](backend/tests/helpers.py) builds a
question by hand for cases the real database has no example of, and
`choice`/`checkbox`/`blank`/`written` build responses.

The real questionnaire exports hold student and alumni names, so they are
gitignored. Tests that need them take the `real_exports` fixture, which **skips**
when the files are absent — so a fresh clone still runs everything that works off
the committed synthetic cohorts.

`pythonpath = [".", "tests"]` in [`pyproject.toml`](backend/pyproject.toml) is
what lets tests import both `app` and `helpers` with no install step.

### `make_synthetic.py` — 756 lines

Generates two synthetic cohorts with deliberately different shapes — A is 16
mentors / 48 mentees (oversubscribed, so mentees are waitlisted), B is 24 / 18
(undersubscribed, so mentors go unused). The real sample is 6 mentors and 4
mentees, which leaves the waitlist, the avoid constraint and wide percentile
calibration all unreachable.

Each cohort is filled at random from the answer pools, then a fixed `EDGE_CASES`
table is written over the first sixteen rows, so the awkward inputs appear by
construction rather than by luck of the seed. Column headers come from the
questions database itself.

### Configuration files

`pyproject.toml` pins `sentence-transformers`, `transformers` and `torch`
**exactly** — these three determine the embedding vectors, so version drift would
silently change similarity scores between runs.

---

## 5. Conventions to keep

### Records, not objects

Every dataclass is `@dataclass(frozen=True)`, and most carry the same phrase:
*"Plain immutable record, no behavior."*

Thirteen of them — five in `inputs.py`, eight in `matching.py`.
[`PairScore`](backend/app/matching.py#L391) is the **only** exception, with two
derived `@property` accessors. When you find yourself wanting a method on a
record, that's the bar to clear.

Mutation happens by copy — `resolve_response` uses `dataclasses.replace` rather
than a method on `Response`.

### Why a function instead of inline code

There are **two distinct reasons**, and they look different in the code.

**Extracted for reuse** — two or more call sites, little or no comment:
[`_cell`](backend/app/inputs.py#L480) (4 call sites),
[`displayed_answer`](backend/app/main.py#L73) (4, across two endpoints),
[`_is_na`](backend/app/inputs.py#L156) (3),
[`_vocabulary_questions`](backend/app/matching.py#L565) (2),
[`name_row`](backend/app/main.py#L67) (2),
[`_options_for`](backend/app/inputs.py#L560) (2).

**Extracted for naming** — one call site, carrying a docstring that explains
reasoning which needed somewhere to live:
[`build_slots`](backend/app/matching.py#L701) (body is one comprehension; the
docstring is the spare-mentor cap), [`build_matrix`](backend/app/matching.py#L717)
(padding and tie-break jitter, both non-obvious), and
[`_points_for`](backend/app/matching.py#L413), which separates *routing* from
*accumulating* so `score_pair`'s loop stays readable.

A third, smaller category: **one-line predicates promoted because the concept
deserves a name.** `is_blank` is `normalize(x) == ""`; `missing_email` is
`not _extract_email(...)`. Both could be inlined everywhere. Both stay because
"blank" and "unreadable address" are domain vocabulary — and `missing_email`
additionally keeps `main.py` from reaching for a private helper in `inputs.py`.

If you're adding code and it doesn't fit any of these three, inline it.

### Private and public track the import boundary

The leading underscore signals *"nothing outside this module should call this,"*
not *"this is only used once."* Several public functions have exactly one call
site (`missing_email`, `resolve_offset`); several private ones have four
(`_cell`).

In `main.py`, which nothing imports, the split means something slightly different:
`_require` is FastAPI plumbing, while the un-prefixed names (`build_report`,
`match_detail`, `displayed_answer`, `name_row`) are response-shaping logic that
the tests import directly.

### Comments explain why, not what

This is the dominant documentation form and the strongest signature of the
codebase's style. Nearly every non-obvious decision has a comment giving the
reason, not a restatement of the code. The CSS drag-and-drop cluster and
`build_slots`'s docstring are the clearest examples.

When you change something these comments describe, change the comment. Several of
them encode browser behaviour or scoring rules that are genuinely hard to
rediscover.

### One dependency-injection seam, and only one

[`Extractor`](backend/app/matching.py#L525) plus
`extract_avoid_terms(..., extractor=keyword_extractor)` is the whole of it.
Everything else is concretely wired — no strategy objects, no registries, no
plugin points. Worth stating explicitly so you don't go looking for a pattern that
isn't there.

### Frontend: derive, don't store

Store the minimum. `pulled` and `manualPairs` are the only manual state; six other
values are recomputed each render. Two representations of the same fact can
disagree; one cannot.

### Errors are values

`send<T>` never throws. `Result<T>` is a discriminated union, so the type system
requires every caller to handle failure before reading data.

---

## 6. Sharp edges

Real characteristics of the code, with the reason each is acceptable — or the
condition under which it stops being.

**`_session` is one global dict.** ([`main.py:45`](backend/app/main.py#L45)) Every
request in the process shares it. No lock, no per-user isolation, no persistence.
Single-tenant by design, and the module docstring argues the trade openly. It
stops being fine the moment two coordinators use one deployment at the same time —
the second upload silently replaces the first's cohort.

**`tsconfig.json` never sets `"strict"`.** So `strictNullChecks` is off, despite
the client leaning heavily on `X | null` state types. Enabling it would be a
one-line change that locks in a property the codebase already has, rather than a
migration. Left as an observation, not a change.

**Manual pairs bypass the avoid constraint.** `blocked` is applied only when
building the solver matrix ([`matching.py:736`](backend/app/matching.py#L736));
the scores dict keeps every pair's true score. So `/api/match` returns a real
percentage for a pair the solver deliberately excluded, and the UI will let you
create it with nothing indicating that. Capacity *is* enforced in manual matching
(the pool only lists mentors with a free place); the avoid constraint is not.
Arguably correct — a manual override is a deliberate act — but it is silent, and
the blocked set is already computed in `run()` if you ever want to surface it.

**The client reads any bodyless 5xx as "the backend is down."**
([`App.tsx:63`](frontend/src/App.tsx#L63)) FastAPI answers an uncaught exception
with plain-text `Internal Server Error` and no JSON, which is indistinguishable
from the dev proxy's reply when nothing is listening — so a genuine server bug is
reported as an outage and sends you off restarting uvicorn. Upload failures no
longer take this path, since they are handled 400s, but any *other* unhandled
exception still will. Fixing it properly means giving the app a handler that
returns JSON for 500s, so a missing body genuinely does mean the proxy.

**`body as T` is a trust boundary, not a validated one.**
([`App.tsx:58`](frontend/src/App.tsx#L58)) The frontend types are hand-maintained
mirrors of the FastAPI responses with no runtime validation and no shared schema.
If a backend response shape changes, TypeScript will not notice — the first sign
will be `undefined` on screen.

**`Results` rebuilds several Maps on every render** with no `useMemo`
([`App.tsx:408-442`](frontend/src/App.tsx#L408-L442)). At cohort scale — tens of
people — this is genuinely free, and memoising would add a dependency array to
keep correct. Named here so nobody assumes it was overlooked.

**`nearest_option` snapping is semantically unreliable.** Write-ins are matched by
embedding similarity, and the result is sometimes wrong in ways a human would not
be: "Blunt is fine, do not cushion it" has been observed snapping to "Both,
depending on the situation". Known behaviour, not a regression. It affects which
listed option a write-in counts as, and the write-in penalty applies regardless.

**Backend changes need a uvicorn restart.** Repeated for emphasis — it has
produced false conclusions in this repo more than once.

---

## Where to start reading

If you want to change scoring, read `matching.py` sections 1–2 and
`test_scoring.py` together — the tests are the clearest statement of the rules.

If you want to change how answers are read, read `inputs.py` sections 2–3 and
`test_inputs.py`.

If you want to change the UI, read `App.tsx` from line 357 down, and
[section 4 above](#apptsx--667-lines) on the derivation.

If you want to add a question, you probably don't need to touch Python at all —
add a row to
[`Mentee_Mentor Questions Database.csv`](Mentee_Mentor%20Questions%20Database.csv)
and regenerate the synthetic cohorts.
