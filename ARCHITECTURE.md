# Architecture

A guide to this repository for someone reading it for the first time.

This is about **how the code is built**, not what the product does. Where the
matching rules come up, they come up because they explain a structural
decision — why a boundary sits where it does, why a value is `None` instead of
`0`, why one function got a name and another didn't.

Every claim below cites `file:line` so you can check it. Line numbers refer to
the commit this document was written against; if one is off by a few, the
function name will still find it.

---

## Contents

1. [Orientation](#1-orientation)
2. [Running it](#2-running-it)
3. [One upload, end to end](#3-one-upload-end-to-end)
4. [Reference, file by file](#4-reference-file-by-file)
   - [`config.py`](#configpy--115-lines)
   - [`inputs.py`](#inputspy--945-lines)
   - [`matching.py`](#matchingpy--970-lines)
   - [`main.py`](#mainpy--357-lines)
   - [`App.tsx`](#apptsx--745-lines)
   - [`index.css`](#indexcss--389-lines)
   - [`main.tsx`](#maintsx--13-lines)
   - [Tests](#tests--1252-lines)
   - [`make_synthetic.py`](#make_syntheticpy--756-lines)
   - [Configuration files](#configuration-files)
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

Seven source files, 5,470 lines including tests. That is small enough that the
organising principle is **fewer, larger files grouped by role** rather than one
file per concept. Following an answer from a CSV cell to a percentage on screen
means reading four files in order, not fifteen.

### The file inventory

| File | Lines | Job |
|---|---|---|
| [`backend/app/config.py`](backend/app/config.py) | 115 | Every tunable constant, plus the one string-normalising function |
| [`backend/app/inputs.py`](backend/app/inputs.py) | 945 | Files → structured, comparable answers |
| [`backend/app/matching.py`](backend/app/matching.py) | 970 | Answers → scores → an assignment |
| [`backend/app/main.py`](backend/app/main.py) | 357 | The five HTTP endpoints and the JSON they return |
| [`frontend/src/App.tsx`](frontend/src/App.tsx) | 745 | The entire client: types, fetch layer, every component |
| [`frontend/src/index.css`](frontend/src/index.css) | 389 | All styling, hand-written, no framework |
| [`frontend/src/main.tsx`](frontend/src/main.tsx) | 13 | Mounts `App` into the DOM |
| [`backend/tests/`](backend/tests/) | 1,252 | 60 tests across four files plus `conftest.py` |
| [`backend/tests/fixtures/make_synthetic.py`](backend/tests/fixtures/make_synthetic.py) | 756 | Generates the two synthetic cohorts |

### The layering

The single most useful thing to know about the backend:

```
config.py  ←  inputs.py  ←  matching.py  ←  main.py
```

**No module imports anything to its right.** This is a strict, acyclic layering,
and it is what makes the codebase navigable — you can read `config.py` knowing
nothing, then `inputs.py` knowing only `config.py`, and so on.

One nuance worth noticing: `main.py` imports from *both* `inputs.py` and
`matching.py` ([`main.py:24-50`](backend/app/main.py#L24-L50)), not just the
layer directly beneath it. That is deliberate — the HTTP layer needs to read
files (`read_export`, `load_questions`) *and* run the solve (`prepare`,
`solve`), so it talks to two stages. It is still a strict layering; it just
isn't a chain.

### The convention that tells you a file's shape

`inputs.py` and `matching.py` both open with a docstring that **enumerates
numbered stages**, then use banner comments that map onto those stages one to
one:

```python
# --- 1. the questions database ------------------------------------------------
# --- 2. the form exports --------------------------------------------------
# --- 3. parsing answers ---------------------------------------------------
# --- 4. embedding and write-ins -------------------------------------------
```

Read the docstring first and you know the file's layout before scrolling. This
holds exactly for `inputs.py` (docstring lists four stages, file has four
banners). For `matching.py` the docstring lists three stages but the file has
four banners — the assignment is presented in prose as the back half of stage 3
but gets its own section in code.

---

## 2. Running it

**Backend** (from `backend/`):

```bash
uv run uvicorn app.main:app        # serves on 127.0.0.1:8000
uv run pytest -q                   # 60 tests
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

When the backend isn't listening, the dev server answers with a gateway error
and no JSON body. `send` detects exactly that case and returns a message
naming the command to fix it ([`App.tsx:86`](frontend/src/App.tsx#L86)).

### One gotcha worth stating loudly

**Backend edits need a uvicorn restart.** There is no reload in the documented
command. A stale server on `:8000` serving code you have already changed has
produced confusing, wrong results more than once in this project's history. If
a backend change appears to have done nothing, check that first.

---

## 3. One upload, end to end

This is the section to read if you only read one. Two CSVs go in; a table of
pairs comes out. Here is every hop.

### Stage 1 — the questions database becomes configuration

Before any response is read, [`load_questions`](backend/app/inputs.py#L315)
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
weight, and (for free-text rows) the percentile cutoffs. Adding a question to
the forms means adding a row here — no Python changes.

`load_questions` assigns each row a `role` via [`_route`](backend/app/inputs.py#L285),
which is the fork that decides everything downstream: `multiple_choice`,
`checkbox`, `semantic`, `location`, `avoid`, or `unscored`.

### Stage 2 — the exports are read and linked

[`read_export`](backend/app/inputs.py#L416) reads a CSV or XLSX with
`dtype=str` — everything stays a string. This matters: a graduation year read as
an integer becomes `2027` and then `"2027"` again with different formatting, and
`test_reads_values_as_text` ([`test_inputs.py:56`](backend/tests/test_inputs.py#L56))
pins it.

Which reader runs is chosen by **the filename extension, not the contents**, so
a file can easily reach the wrong one. Anything unreadable becomes an
[`ExportReadError`](backend/app/inputs.py#L394) rather than whatever pandas
threw, tagged with one of two kinds: `READ_WRONG_TYPE` for a file that is not
the format its name claims, and `READ_MALFORMED` for text that has a header but
whose rows do not line up with it. The endpoint turns each into different
advice, because the first needs a different file and the second needs the sheet
tidying up. Both are a 400 — an unreadable upload is the uploader's problem,
not the server's.

[`link_columns`](backend/app/inputs.py#L461) then pairs each database row to a
column in each export **by matching question text, not by column position**.

This is the first real design decision worth pausing on. Google Forms exports
columns in whatever order the form is currently in. Keying on position would
mean that reordering questions silently rescores everyone.
`test_linking_ignores_column_order` ([`test_inputs.py:61`](backend/tests/test_inputs.py#L61))
shuffles the columns and asserts the mapping is unchanged.

When a question can't be found, `link_columns` raises
[`ExportLinkError`](backend/app/inputs.py#L370) carrying **every** unresolved
question, not just the first. That list survives all the way to the browser —
it is the reason the error type exists at all, and the reason `Result<T>` on the
frontend has a `missing?` field.

**Before trusting that error, `upload` tries the two frames the other way
round.** The two forms word most of their questions differently, so a pair that
will not link one way and links cleanly the other way is the same two files in
the wrong two boxes — the likeliest mistake a coordinator can make, and one
that otherwise answers with 37 missing questions instead of one sentence. The
linker is the judge, so this is a proof rather than a guess: if the swapped
order links, the run goes ahead swapped and the response carries
`{"swapped": true}` for the client to mention. If neither order links, the
files really are mismatched and the original error is raised untouched.

### Stage 3 — rows become people

[`build_respondents`](backend/app/inputs.py#L587) turns a dataframe into
`Respondent` records, keyed by email address, collapsing resubmissions so the
latest wins ([`_is_newer`](backend/app/inputs.py#L654)).

Email is the identity key, which is why a respondent without a readable one is
the single thing flagged for coordinator review
([`missing_email`](backend/app/inputs.py#L550)) — not because the address is
needed, but because duplicate submissions from that person can't be detected.

### Stage 4 — answers become option indices

[`parse_responses`](backend/app/inputs.py#L778) resolves each answer cell
against its question's option list, once. After this point **everything
downstream compares integers, not text.**

That single decision is what lets the two forms word the same option
differently. Row 9 asks about feedback preferences with different phrasing on
each side; because each side is matched against its own option list, the same
underlying choice lands on the same index either way, and
`test_differently_worded_options_align_by_index`
([`test_inputs.py:190`](backend/tests/test_inputs.py#L190)) proves it.

Anything matching no listed option is carried forward as a **write-in**, kept as
raw text on the `Response`.

### Stage 5 — one embedding pass

[`build_cache`](backend/app/inputs.py#L854) gathers every distinct string that
will need a vector ([`collect_texts`](backend/app/inputs.py#L803)) and embeds
them all in one batch ([`embed`](backend/app/inputs.py#L838)).

The vectors are unit length, so cosine similarity reduces to a dot product
([`similarity`](backend/app/inputs.py#L861)). Embedding once and reusing is the
difference between one model pass and one per pair.

`similarity` raises `KeyError` on a cache miss rather than recomputing. That is
intentional: a miss means `collect_texts` has a bug, and silently papering over
it would make the bug invisible. `test_uncollected_string_raises`
([`test_inputs.py:245`](backend/tests/test_inputs.py#L245)) locks that in.

Then [`resolve_write_ins`](backend/app/inputs.py#L922) snaps each write-in to
the listed option it most resembles ([`nearest_option`](backend/app/inputs.py#L879)),
while keeping the original text — its presence is what triggers the write-in
penalty later ([`penalty`](backend/app/inputs.py#L937)).

### Stage 6 — cohort-wide calibration

[`calibrate`](backend/app/matching.py#L145) derives similarity cutoffs for each
free-text question **from the cohort's own distribution** of scores.

A fixed cutoff can't work here: what counts as a "similar" answer depends
entirely on what people wrote this cycle. So each semantic question gets its own
`Cutoffs` from percentiles of the actual pairwise similarities.

[`resolve_offsets`](backend/app/matching.py#L408) does the equivalent for
location, turning free-text places into hours-from-Pacific.

All of this lands in a [`ScoringContext`](backend/app/matching.py#L467) — the
values that depend on *who submitted*, computed once and threaded explicitly
through scoring rather than stashed in a global.

`prepare` ([`matching.py:604`](backend/app/matching.py#L604)) runs stages 3–6 in
order and hands back `(mentors, mentees, context)`.

### Stage 7 — every pair is scored

[`score_all`](backend/app/matching.py#L589) scores the **full** mentor × mentee
matrix. Not the pairs that look promising — all of them.

[`score_pair`](backend/app/matching.py#L541) sums each scored question's points
× weight, subtracts write-in penalties, and divides by the maximum achievable
**on the questions both parties actually answered**.

Two things to internalise:

**`None` is not `0`.** Each scorer returns 10, 5, 0, or `None`. `None` means the
question can't be scored for this pair, and drops out of *both* the numerator
and the denominator. So skipping an optional question costs nothing, while
disagreeing costs real points. Two tests sit directly on this distinction:
`test_a_skipped_question_leaves_the_ratio_untouched` and
`test_a_disagreement_is_not_the_same_as_a_skip`
([`test_scoring.py:293`](backend/tests/test_scoring.py#L293), [`:307`](backend/tests/test_scoring.py#L307)).

**The ratio ranks, not the raw total.** Otherwise a pair would rank higher
merely for having had more questions in common to earn points on.

### Stage 8 — the avoid constraint

[`build_vocabulary`](backend/app/matching.py#L713) builds a closed vocabulary
from the surveys themselves, then [`extract_avoid_terms`](backend/app/matching.py#L753)
resolves each person's "what would you rather avoid" answer against it, once —
not per pair.

[`blocked_cells`](backend/app/matching.py#L810) produces the set of
`(mentor_key, mentee_key)` pairs where one side works on what the other asked to
avoid. Matching is **exact, not partial**: "investment banking" does not match
"banking", per `test_matching_is_exact_not_partial`
([`test_matching.py:130`](backend/tests/test_matching.py#L130)).

### Stage 9 — the global solve

[`build_slots`](backend/app/matching.py#L863) expands mentors into one entry per
opening. [`build_matrix`](backend/app/matching.py#L884) builds the padded score
matrix. [`solve`](backend/app/matching.py#L913) runs the Hungarian algorithm via
`scipy.optimize.linear_sum_assignment`.

**Global, not greedy.** Taking the best pair, then the next best, looks
reasonable and isn't: an early pair claims a mentor a later mentee needed far
more, and the cohort ends up worse overall.
`test_the_global_solve_beats_picking_greedily`
([`test_matching.py:171`](backend/tests/test_matching.py#L171)) constructs
exactly that situation with hand-built scores.

Blocked pairs get [`BLOCKED_SCORE`](backend/app/matching.py#L839) (`-1.0e6`)
rather than being removed from the matrix. A finite penalty keeps the problem
solvable — a mentee blocked from everyone lands on the waitlist instead of
making the solve infeasible
(`test_a_fully_blocked_mentee_is_waitlisted_rather_than_forced`,
[`test_matching.py:206`](backend/tests/test_matching.py#L206)).

Ties get deterministic jitter of [`TIE_BREAK_RANGE`](backend/app/matching.py#L842)
(`1.0e-9`) seeded from `RANDOM_SEED`, so identical inputs give identical output
across runs.

### Stage 10 — JSON, and the client

[`build_report`](backend/app/main.py#L106) assembles the response: `matches`,
`waitlist`, `unmatched_mentors`, `review_flags`.

The client's [`send<T>`](frontend/src/App.tsx#L88) receives it and returns a
`Result<T>`. `App` stores it in one `report` state ([`App.tsx:154`](frontend/src/App.tsx#L154)),
and `Results` **derives everything else from it on every render** — the live
match list, mentor usage counts, and both manual-review pools
([`App.tsx:445-487`](frontend/src/App.tsx#L445-L487)).

### Where a manual match gets its score

Because stage 7 scored *every* pair, a pair the solver never chose already has a
real score sitting in the session. When the coordinator drags a mentee onto a
mentor, `handlePair` ([`App.tsx:249`](frontend/src/App.tsx#L249)) calls
`GET /api/match/{mentor}/{mentee}`, and the endpoint does a **dict lookup**:

```python
_session["scores"].get((mentor_key, mentee_key))
```

No recomputation, no second code path. Same `score_pair`, same weights, same
cohort-wide calibration — which is also why solver and manual percentages are
directly comparable and can be sorted into one table.

---

## 4. Reference, file by file

In dependency order. Every top-level definition appears. Anything with
non-obvious reasoning gets a paragraph; the rest are one-line table rows.

### `config.py` — 115 lines

Constants and one function. Everything here is a constant rather than a runtime
setting, so a run is reproducible from its inputs alone.

The interesting part is the bottom. `normalize` and `is_blank` exist because
**the database and the exports differ in invisible ways**: the questions CSV
contains non-breaking hyphens, and Forms exports routinely carry smart quotes
and trailing spaces. Comparing raw strings reads those as mismatches — a listed
option would look like a write-in and earn an undeserved penalty, and a question
would look missing from its export.

| Definition | Line | Note |
|---|---|---|
| `QUESTIONS_DATABASE` | 21 | Path to the questions CSV, relative to the repo root |
| `RANDOM_SEED` | 27 | Seeds the solver's tie-break jitter |
| `EMBEDDING_MODEL` | 29 | `all-mpnet-base-v2` |
| `PERFECT_MATCH_POINTS` / `GOOD_MATCH_POINTS` / `NO_MATCH_POINTS` | 32-34 | The 10 / 5 / 0 scale |
| `WRITE_IN_PENALTY` | 38 | Flat 5-point deduction |
| `DEFAULT_PERCENTILES` | 41 | `(85, 50)` fallback |
| `LOCATION_PERFECT_MAX_HOURS` / `LOCATION_GOOD_MAX_HOURS` | 44-45 | Time-zone bands |
| `DISPLAY_ORDER` | 52 | Question order a coordinator reads, by row number |
| `LOCATION_QUESTION_PREFIX` / `AVOID_QUESTION_PREFIX` / `MENTEE_CAPACITY_QUESTION` | 58-60 | Text used to route special rows |
| `NAME_QUESTION` / `EMAIL_QUESTION_KEYWORD` | 65-66 | Identity-field lookup |
| `DEFAULT_MENTOR_CAPACITY` | 69 | Falls back to 1 |
| `VOCABULARY_QUESTIONS` | 73 | Which questions seed the avoid vocabulary |
| `MIN_VOCABULARY_TERM_LENGTH` / `MAX_VOCABULARY_TERM_WORDS` | 82-83 | Vocabulary filters |
| `_DASH_VARIANTS` … `_WHITESPACE_RUN` | 86-94 | Private; build `normalize`'s translate table |
| **`normalize(text)`** | 97 | Canonical comparison form: NFKC, ASCII-ify quotes and dashes, collapse whitespace, casefold |
| **`is_blank(text)`** | 113 | `normalize(text) == ""` |

The rule to remember: **original text is always kept for display; only
comparisons use the normalized form.** A coordinator opening a match sees what
the person actually typed.

`is_blank` is a one-line wrapper that could be inlined at all eight of its call
sites. It has a name because "blank" is a domain concept — a cell with no
response — and the name documents each call site without a comment. See
[why a function](#why-a-function-instead-of-inline-code).

### `inputs.py` — 945 lines

Files → structured, comparable answers. Four banner sections matching the four
stages in its docstring.

#### Section 1 — the questions database (line 52)

Parses the configuration CSV. This is the densest cluster of private helpers in
the codebase, because the criteria column has a small grammar of its own
(`{10: Yes & Yes, No & No; 5: Maybe & Yes; 0: Yes & No}`).

| Definition | Line | Note |
|---|---|---|
| `ROLE_*` constants | 56-61 | `unscored`, `multiple_choice`, `checkbox`, `semantic`, `location`, `avoid` |
| `NATURAL_LANGUAGE_MARKER` | 63 | `{natural language input}` marks a write-in option |
| `_OPTION_NUMBER` / `_SCORE_LABEL` / `_LEADING_INT` | 65-67 | Private; the regexes that read the criteria grammar |
| `Option` | 71 | Frozen record: `index`, `text`, `is_write_in` |
| `Question` | 80 | Frozen record, 12 fields — one database row |
| `_strip_braces` | 101 | Drops the outer `{ }` |
| `_is_na` | 109 | Blank or literal `"na"` |
| `_parse_required` | 113 | Per-side required flags |
| `_parse_percentiles` | 126 | `"85/50"` → `(85, 50)` |
| `_parse_options` | 139 | Option-list cell → `Option` tuple |
| `_option_index_lookup` | 168 | Normalized option text → indices, both sides |
| `_lookup_side` | 185 | Resolve one `"A \| B"` alternative |
| `_split_shared_chunk` | 197 | Find the right comma-split point |
| `_parse_combinations` | 212 | Split an `&`-joined segment into side pairs |
| `_score_segments` | 230 | Split criteria into 10/5/0 chunks |
| `_parse_choice_scores` | 242 | Build the `(mentor idx, mentee idx) → points` table |
| `_parse_overlap_thresholds` | 275 | Build `(min overlap, points)` for checkboxes |
| `_route` | 285 | **Decide a row's role** — the fork everything downstream keys off |
| **`for_display`** | 305 | Sort questions into reading order via `DISPLAY_ORDER` |
| **`load_questions`** | 315 | The whole loader; composes everything above |

#### Section 2 — the form exports (line 364)

| Definition | Line | Note |
|---|---|---|
| `TIMESTAMP_HEADER` | 367 | `"Timestamp"` |
| `ExportLinkError` | 370 | Carries **every** unresolved question, not the first |
| `READ_WRONG_TYPE` / `READ_MALFORMED` | 390-391 | The two ways an upload fails before any question is looked at |
| `ExportReadError` | 394 | Raised instead of pandas' own exceptions, carrying which of the two it was |
| `ColumnLink` | 408 | Frozen record: which column answers which row, per side |
| **`read_export`** | 416 | CSV/XLSX → dataframe, `dtype=str` throughout |
| `_header_lookup` | 453 | Normalized header → original header |
| **`link_columns`** | 461 | Match questions to columns by text; raise on any miss |
| `MENTOR` / `MENTEE` | 500-501 | The two side tags used everywhere |
| `_EMAIL_PATTERN` / `_NUMBER_WORDS` | 505-507 | Private; extract an address, and read `"Two"` as a number |
| `Respondent` | 511 | Frozen record: `key`, `side`, `name`, `email`, `capacity`, `submitted_at`, `responses` |
| `_question_text` | 524 | Pick mentor or mentee wording |
| `_column_for` | 528 | Pick mentor or mentee column |
| `_find_question` | 532 | First question satisfying a predicate — takes the predicate as a callable |
| `_extract_email` | 542 | Pull an address out of noisy text |
| **`missing_email`** | 550 | Whether the address is unreadable |
| `_parse_capacity` | 560 | `"Two"` or `"2"` → `2` |
| `_cell` | 571 | One cell as display text, blank → `""` |
| `_timestamps` | 579 | Parse the timestamp column, or an all-NaT series if absent |
| **`build_respondents`** | 587 | Assemble and deduplicate one side |
| `_is_newer` | 654 | The dedup tie-break |

`_find_question` is worth a look: it takes `matches` as a plain callable, which
lets `build_respondents` reuse one search for three different lookups (name,
email, capacity) by passing three lambdas.

#### Section 3 — parsing answers (line 623)

| Definition | Line | Note |
|---|---|---|
| `KIND_*` constants | 667-670 | `blank`, `choice`, `checkbox`, `text` |
| `Response` | 674 | Frozen record: `row`, `kind`, `text`, `indices`, `write_ins` |
| `_options_for` | 687 | Pick this side's option list |
| `_index_lookup` | 691 | Normalized option text → index, excluding write-in slots |
| `_split_checkbox` | 705 | **Split a checkbox cell without breaking options that contain commas** |
| `_parse_choice` | 729 | Resolve a multiple-choice cell |
| `_parse_checkbox` | 740 | Resolve a checkbox cell |
| **`parse_response`** | 758 | Dispatch one cell by `question.role` |
| **`parse_responses`** | 778 | Every answer of one respondent |

`_split_checkbox` handles a genuinely nasty case. Forms joins checkbox
selections with `", "`, but an option's own text can contain a comma —
`"Both, depending on the day"`. A naive split produces two phantom selections.
The helper rejoins chunks that only make sense together, and
`test_checkbox_option_containing_a_comma_is_not_split`
([`test_inputs.py:227`](backend/tests/test_inputs.py#L227)) guards it.

#### Section 4 — embedding and write-ins (line 749)

| Definition | Line | Note |
|---|---|---|
| **`load_model`** | 794 | `@lru_cache(maxsize=1)`; imports `sentence_transformers` **inside the function** |
| **`collect_texts`** | 803 | Every distinct string that needs a vector |
| **`embed`** | 838 | One batched model pass |
| **`build_cache`** | 854 | `embed(collect_texts(...))` |
| **`similarity`** | 861 | Dot product of two cached unit vectors; `KeyError` on a miss |
| `_RESOLVABLE` | 876 | The roles a write-in can be snapped for |
| **`nearest_option`** | 879 | Best-matching listed option for a write-in |
| **`resolve_response`** | 901 | Attach resolved indices to one answer |
| **`resolve_write_ins`** | 922 | The same across a whole answer set |
| **`penalty`** | 937 | 0 or `WRITE_IN_PENALTY`, once, regardless of side |

The deferred import in `load_model` is deliberate: pulling in torch costs
seconds, and the API server shouldn't pay that just to serve an upload page.

### `matching.py` — 970 lines

Scoring, the avoid constraint, and the assignment. Four banner sections.

#### Section 1 — scoring one question (line 72)

Five scorers, each returning 10, 5, 0, or `None`.

| Definition | Line | Note |
|---|---|---|
| **`score_multiple_choice`** | 74 | Look the chosen pair up in `question.choice_scores` |
| **`score_checkbox`** | 97 | Score on overlap count vs thresholds |
| `Cutoffs` | 111 | Frozen record: `upper`, `lower`, `pair_count` |
| `_answered` | 119 | Non-blank *and* non-whitespace |
| **`similarities`** | 129 | Every answered mentor × mentee similarity for one question |
| **`calibrate`** | 145 | Derive each semantic question's cutoffs from the cohort |
| **`score_semantic`** | 185 | Score one pair against those derived cutoffs |
| `_ZONES_BY_NAME` … `_MAX_PLAUSIBLE_HOURS` | 211-343 | The time-zone tables and the regexes that read a location |
| `LocationOffset` | 347 | Frozen record: `hours`, `source` |
| `_stated_offset` | 356 | Parse an explicitly stated difference |
| `_looked_up_offset` | 372 | Resolve a place name or state code |
| **`resolve_offset`** | 392 | Stated first, then looked up |
| **`resolve_offsets`** | 408 | Every respondent, logging the unreadable ones |
| **`score_location`** | 434 | Score the gap between two offsets |

`LocationOffset.source` records *how* the offset was determined — stated by the
respondent, or inferred from a table. That distinction is what you want when a
match looks wrong and you need to know whether the system guessed.

Location parsing is where the real-world messiness lives: a ZIP code must not
read as a negative offset, and `"LA"` must resolve as a state only when it is a
whole comma-delimited segment, not inside another word. Both are pinned
([`test_scoring.py:195`](backend/tests/test_scoring.py#L195), [`:199`](backend/tests/test_scoring.py#L199)).

#### Section 2 — scoring one pair (line 450)

| Definition | Line | Note |
|---|---|---|
| `SCORED_ROLES` | 455 | Deliberately excludes `ROLE_AVOID` — it constrains, it doesn't score |
| `Participant` | 459 | Frozen record: a `Respondent` plus their parsed answers |
| `ScoringContext` | 467 | Frozen record: questions, cache, cutoffs, offsets — the cohort-wide values |
| `QuestionScore` | 481 | Frozen record: `row`, `penalty`, `contribution`, `maximum` |
| **`PairScore`** | 491 | Frozen record **plus two properties** — the one exception to the house style |
| `_points_for` | 513 | Route one question to its scorer |
| **`score_pair`** | 541 | One mentor against one mentee across every scored question |
| **`score_all`** | 589 | The full matrix |
| **`prepare`** | 604 | Runs the whole input pipeline and packages the result |

`PairScore` ([`matching.py:491`](backend/app/matching.py#L491)) is the only
dataclass in the codebase carrying behaviour: `percentage` (line 503) and
`scored_questions` (line 507). Both are derived views over its own fields, and
both are read — `percentage` by `main.py`, `scored_questions` by
[`test_scoring.py:304`](backend/tests/test_scoring.py#L304).

Note also that `PairScore.normalized` can be negative: write-in penalties come
off the raw total and nothing clamps it.

#### Section 3 — the avoid constraint (line 645)

| Definition | Line | Note |
|---|---|---|
| **`Extractor`** | 649 | Type alias naming a function shape — **the one DI seam in the codebase** |
| `NULL_ANSWERS` | 653 | Canonical "nothing to avoid" phrasings |
| `JUNK_TERMS` | 662 | Filler excluded from the vocabulary |
| `_ENUMERATION` / `_TERM_SEPARATORS` / `_PARENTHETICAL` | 672-675 | Splitting regexes |
| `_clean_terms` | 678 | One cell → filtered candidate terms |
| `_vocabulary_questions` | 702 | Questions that seed the vocabulary |
| **`build_vocabulary`** | 713 | The closed vocabulary, from the surveys themselves |
| **`is_null_answer`** | 738 | Whether an answer means "nothing" |
| **`keyword_extractor`** | 743 | The default `Extractor`: whole-word regex match |
| **`extract_avoid_terms`** | 753 | Resolve everyone's avoid terms once |
| **`stated_terms_for_all`** | 788 | What each person says they *work on*, for the reverse check |
| **`blocked_cells`** | 810 | The excluded `(mentor, mentee)` pairs |

The vocabulary is **closed** — drawn from the surveys, filtered by length and
word count — rather than open-ended text matching. That is what keeps "R" and
"data" from blocking half the cohort
(`test_vocabulary_drops_terms_too_generic_to_match_on`,
[`test_matching.py:76`](backend/tests/test_matching.py#L76)).

The constraint is checked in **both directions**: a mentee's stated preference
blocks the pair just as a mentor's does
([`test_matching.py:119`](backend/tests/test_matching.py#L119)).

#### Section 4 — the assignment (line 835)

| Definition | Line | Note |
|---|---|---|
| `BLOCKED_SCORE` | 839 | `-1.0e6` — finite, so a blocked mentee waitlists rather than breaking the solve |
| `TIE_BREAK_RANGE` | 842 | `1.0e-9` — smaller than any real score gap |
| `Assignment` | 846 | Frozen record: mentor, mentee, score |
| `Solution` | 855 | Frozen record: assignments plus unassigned |
| **`build_slots`** | 863 | One entry per opening, capacity-aware |
| **`build_matrix`** | 884 | The padded, jittered matrix |
| **`solve`** | 913 | Hungarian algorithm, then translate back to domain objects |

[`build_slots`](backend/app/matching.py#L863) is the best example in the
codebase of a function that exists to give reasoning a home. Its body is a
single comprehension. Its docstring is twelve lines, explaining a subtle rule:
when there are at least as many mentors as mentees, every mentor is capped at
one mentee instead of their stated capacity.

The reason is that a capacity-2 mentor is *two identical columns* in the matrix.
Nothing in the solve prefers spreading, and attractiveness is a property of the
mentor rather than the slot — so a popular mentor's two openings both fill while
another mentor gets nobody. Capping only when mentors are spare means no mentee
is waitlisted for it.

### `main.py` — 357 lines

The HTTP surface. Deliberately small. Two sections.

State lives in a **module-level dict** ([`main.py:66`](backend/app/main.py#L66)):

```python
_session: dict = {}
```

Restarting the server loses an uploaded cohort. The module docstring argues this
is the right trade for a tool one coordinator runs a few times a cycle. See
[sharp edges](#6-sharp-edges) for what would break it.

Note also what is *not* here: manual adjustments live entirely in the frontend,
layered over the report rather than fed back into the solver. Nothing in
`main.py` knows they exist.

| Definition | Line | Note |
|---|---|---|
| `app` | 54 | The FastAPI instance; CORS allows the Vite dev origin |
| `_session` | 66 | The one piece of module-level mutable state |
| `_read_upload` | 69 | `UploadFile` → dataframe |
| `_require` | 73 | Fetch a session key or raise 409 |
| `NO_EMAIL_REASON` | 82 | The single review-flag reason |
| `READ_ADVICE` | 86 | What to tell somebody whose upload could not be read, per failure kind |
| `_flags` | 97 | Build the `review_flags` list |
| **`build_report`** | 106 | The whole `/api/run` response |
| **`name_row`** | 157 | Which database row asks for the name |
| **`displayed_answer`** | 165 | One answer as it should be read |
| **`match_detail`** | 180 | Both people's answers side by side |
| `upload` | 218 | `POST /api/upload` — links, or retries swapped (see below) |
| `run` | 276 | `POST /api/run` — the slow call |
| `match` | 302 | `GET /api/match/{mentor_key}/{mentee_key}` |
| `person` | 321 | `GET /api/person/{key}` |
| `health` | 356 | `GET /api/health` |

`displayed_answer` encodes one specific UX rule: every row shows what was typed,
except the name row when it was left blank, which falls back to whatever
identifies the person. Someone who skipped the name question is still named on
the leaderboard, so showing them an empty cell here would only raise the
question of whose row it is.

The response dicts are built **directly, not modelled as dataclasses and copied
field by field**. There is one producer and one consumer; the extra layer bought
nothing.

### `App.tsx` — 745 lines

The entire client. Five banner sections. No router, no state library, no data-
fetching library, no component directory.

That is a deliberate scale choice, not an accident — and `main.tsx` staying
separate is the one concession, for a mechanical reason
([see below](#maintsx--13-lines)).

#### Types (line 10)

Nine types mirror backend JSON: `MissingQuestion`, `WaitlistEntry`,
`UnmatchedMentor`, `ReviewFlag`, `Report`, `QuestionRow`, `MatchDetail`,
`PersonDetail`, and `Match`.

`Match` ([`App.tsx:18`](frontend/src/App.tsx#L18)) is the exception — it carries
`manual?: true`, which **the backend never sends**. It marks pairs the
coordinator made by hand, so one table can render both kinds.

Two types are purely frontend constructs: `Result<T>` (line 14) and `Failure`
(line 141).

#### The network layer (lines 86-136)

[`send<T>`](frontend/src/App.tsx#L88) is the only place `fetch` is called. It
**never throws** — it always resolves to a `Result<T>`:

```ts
type Result<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; missing?: MissingQuestion[] }
```

Because it's a discriminated union, every caller must branch on `.ok` before
touching `.data`. Errors are values, not control flow.

It folds four distinct failure modes into one shape: a thrown network error, a
gateway error with no JSON (backend not running → the `OFFLINE` message), a
structured error object (the upload's missing-question list), and a plain string
detail.

| Definition | Line | Note |
|---|---|---|
| `flagReasons` | 76 | Group `review_flags` by key — one person can trip several |
| `OFFLINE` | 86 | The message naming the command to start the backend |
| `send<T>` | 88 | The only `fetch` call site |
| `uploadExports` | 116 | `POST /api/upload` with `FormData` |
| `runMatching` | 126 | `POST /api/run` |
| `openMatch` | 130 | `GET /api/match/…` |
| `openPerson` | 138 | `GET /api/person/…` |
| `Uploaded` | 114 | The upload response: whether the two files were read swapped |
| `SWAPPED_NOTICE` | 149 | The wording for that, owned by the client rather than the API |
| `pairKey` | 151 | `` `${mentor}|${mentee}` `` — pair identity everywhere |

#### `App` (line 145) — the state owner

Nine `useState` calls, no `useMemo`, no `useRef`.

| State | Line | Holds |
|---|---|---|
| `report` | 154 | The solver's output |
| `detail` | 155 | Open match overlay |
| `person` | 156 | Open person overlay |
| `error` | 157 | Last failure |
| `notice` | 160 | Something the upload corrected by itself |
| `busy` | 161 | Upload+run in flight |
| `pulled` | 166 | Pair keys pulled apart into manual review |
| `manualPairs` | 167 | Pairs made by hand |
| `history` | 172 | Undo snapshots |

`App` is the only component that calls the network wrappers. `Upload` and
`Results` receive callbacks and never fetch.

The `error` state drives two render locations depending on its shape: an error
carrying `missing` renders *inside* `Upload` as a question list; a plain-string
error renders as its own panel ([`App.tsx:278`](frontend/src/App.tsx#L278), [`:284`](frontend/src/App.tsx#L284)).

**Undo stores snapshots, not inverse actions**
([`App.tsx:169-177`](frontend/src/App.tsx#L169-L177)). `remember()` deep-copies
`pulled` and `manualPairs` onto a stack before each change. They are small, and
it means any future action becomes undoable without anyone writing its inverse.

#### `Upload` (line 310)

Owns its own file-selection state. The one non-obvious bit is `formKey`
(line 325): bumping it remounts the form, which is what actually empties native
file inputs — setting state to `null` leaves the chosen filenames on screen.

#### `Results` (line 409) — the derivation

The heart of the frontend's design. Only `pulled` and `manualPairs` are stored;
**everything the manual area shows is recomputed from them each render**
([`App.tsx:445-487`](frontend/src/App.tsx#L445-L487)):

- `mentors` / `mentees` — lookup maps built from the report's own lists
- `active` — `report.matches` minus `pulled`, plus `manualPairs`, sorted by score
- `used` — how many pairs each mentor currently holds
- `taken` — mentees already placed
- `poolMentors` — every mentor where `used < capacity`
- `poolMentees` — every mentee not in `taken`

The payoff, stated in the source: *"the pool can never disagree with the table."*
Both come from the same two sets in the same render pass, so there is no second
copy of the truth to drift.

Note `poolMentors` keeps a mentor with a place left *even while matched to
someone else* — that is what lets a capacity-2 mentor take a second mentee by
hand.

| Definition | Line | Note |
|---|---|---|
| `toMatch` | 394 | `MatchDetail` → a `Match` row, tagged `manual` |
| `Flag` | 406 | The glyph span; CSS draws the tooltip from `data-reasons` |
| `ResultsProps` | 416 | The prop contract |
| `Results` | 428 | Matches table plus the drag-and-drop board |

#### The overlays (line 631)

| Definition | Line | Note |
|---|---|---|
| `Sheet` | 652 | Shared modal chrome; click-outside closes via `stopPropagation` |
| `MatchSheet` | 681 | Both sides' answers; returns `null` when closed |
| `PersonSheet` | 716 | One person's answers |

### `index.css` — 389 lines

Hand-written global CSS. No modules, no CSS-in-JS, no framework.

#### The token block (lines 1-33)

Each colour has exactly one job, and the comments say so:

- `--text`, `--muted`, `--bg`, `--panel`, `--border` — neutrals, deliberately
  *warm* rather than grey so they sit with the accents
- `--accent`, `--accent-soft` — teal, **the only colour that carries meaning**:
  it marks what responds to you (hover, focus, drop targets)
- `--flag` — orange, only the review-flag glyph
- `--manual`, `--manual-soft` — pink, only the hand-made-pair tag
- `--drop` — pale yellow, only the drag-over highlight
- `--error`, `--error-bg` — red, **reserved for actual breakage**
- `--notice`, `--notice-bg` — warm brown on cream, for a mistake the app put
  right by itself; not red, because the run went ahead

That last one is the rule that keeps the palette honest: decorative colours
never mean "something is wrong."

**Light only, deliberately** (`color-scheme: light`, line 26). There is no
`prefers-color-scheme` block anywhere; the only media query is a 40rem layout
breakpoint (line 385).

#### Naming convention

Flat semantic classes, no BEM. Modifiers are **compound selectors** composed in
JSX template strings:

```tsx
className={`card draggable${lifted === mentee.key ? ' lifted' : ''}`}
```

giving `.card.draggable`, `.card.lifted`, `.card.over`, `.tag.manual`.
Descendant selectors (`.panel header`, `.sheet header`) are preferred over
inventing new class names when the context is already unambiguous.

#### The drag-and-drop rules

These carry the densest comments in the repo, because each encodes a browser
behaviour that is invisible in the code:

- **`.flag::after` uses `display: none`, not `visibility: hidden`**
  (line 239). A hidden box still counts toward what the card overflows, and that
  overflow region is what the browser photographs for the drag image — so an
  invisible tooltip made dragging a flagged card appear to pick up the card
  below it.
- **`.card:active .flag::after { display: none }`** (line 274) covers grabbing a
  card *by the flag*, when the tooltip genuinely is open. `:active` applies on
  mousedown, before `dragstart` fires.
- **`.card.lifted` uses `visibility: hidden`** (line 322) — the opposite choice,
  for the opposite reason. The slot must stay open so cards below don't shuffle
  up under the pointer mid-drag.
- **`.card.draggable { user-select: none }`** (line 307) — otherwise a press
  that doesn't become a drag selects text across neighbouring cards.
- **`.scroll { padding-bottom: 3.5rem }`** (line 166) is not decorative spacing;
  it reserves room so the last row's tooltip isn't clipped by the scroll box.

Two matching subtleties live in the JSX rather than the CSS: `preventDefault()`
in `onDragOver` is what marks an element as a valid drop target, and `setLifted`
is deferred by one `requestAnimationFrame` because the browser snapshots the
drag image synchronously at `dragstart` — hiding the card first would leave
nothing to photograph.

### `main.tsx` — 13 lines

Mounts `App` in `StrictMode`. Its comment explains why it isn't merged into
`App.tsx`:

> Entry point. Kept separate from App.tsx so that file has an export, which is
> what lets Vite hot-reload edits without dropping the loaded report.

Fast Refresh only preserves component state in files that export components. If
`App.tsx` had only a `createRoot` call, every edit would drop the loaded report
and force a re-upload. This is the sole reason the file exists.

### Tests — 1,252 lines

**60 tests** across four files, plus `conftest.py`. Split by layer:

| File | Tests | Layer |
|---|---|---|
| [`test_inputs.py`](backend/tests/test_inputs.py) | 16 | Reading, linking, parsing, embedding |
| [`test_matching.py`](backend/tests/test_matching.py) | 17 | Vocabulary, avoid constraint, solver, report |
| [`test_scoring.py`](backend/tests/test_scoring.py) | 16 | Per-question scorers, calibration, pair assembly |
| [`test_api.py`](backend/tests/test_api.py) | 10 | The HTTP surface |

#### The privacy gate

[`conftest.py`](backend/tests/conftest.py) defines exactly one fixture,
`real_exports` (line 21, session-scoped). It checks whether the two real
questionnaire CSVs exist and calls `pytest.skip` if not.

Because that happens inside a fixture, every test requesting it — directly or
transitively — is **skipped rather than failed**. A fresh clone with no private
data still runs the whole synthetic-cohort suite. This is the mechanism that
makes the `.gitignore` arrangement workable:

> Real questionnaire exports. These hold students' and alumni names and email
> addresses, so they stay on local machines.

#### Fixture scoping is about model cost

Loading the sentence-transformers model takes seconds. Anything that triggers it
is **module-scoped** so it happens once per file rather than once per test:
`ran` ([`test_api.py:31`](backend/tests/test_api.py#L31)), `small_cache` and
`real_run` ([`test_inputs.py:235`](backend/tests/test_inputs.py#L235), [`:316`](backend/tests/test_inputs.py#L316)),
`synthetic_run` ([`test_matching.py:249`](backend/tests/test_matching.py#L249)).

`test_scoring.py` sidesteps the model entirely — `spread_cache`
([`test_scoring.py:130`](backend/tests/test_scoring.py#L130)) builds unit vectors
spaced evenly around a circle, giving cosines you can compute by hand. Exact,
deterministic, instant.

#### Stand-in builders

There is no mocking library anywhere. Instead each file defines small builders
that construct real domain objects with known values:

| Helper | Location | Builds |
|---|---|---|
| `checkbox_question` | `test_inputs.py:156` | A checkbox `Question` the real database has no example of |
| `question_with` | `test_inputs.py:251` | A `Question` with controllable write-in flags |
| `cache_from` | `test_inputs.py:273` | An embedding cache with exact hand-written vectors |
| `person` | `test_matching.py:106` | A minimal `Respondent` — blocking works on keys only |
| `participant` | `test_matching.py:141` | A minimal `Participant` |
| `table` | `test_matching.py:156` | A score table straight from normalized values |
| `cohort` / `run` | `test_matching.py:52`, `:229` | Real pipeline output from two CSVs |
| `report_for` | `test_matching.py:273` | `build_report` over a hand-built `Solution` |
| `choice` / `checkbox` / `blank` / `answer` | `test_scoring.py:63`, `:67`, `:73`, `:251` | `Response` objects |
| `semantic_question` / `yes_no_question` | `test_scoring.py:104`, `:229` | Stand-in `Question`s |
| `spread_cache` | `test_scoring.py:130` | Predictable cosines |
| `context` | `test_scoring.py:276` | A `ScoringContext` with empty cache and cutoffs |

Two files define their own `participant` rather than sharing one via `conftest`.
That is intentional — they are lightweight local stand-ins, not a shared factory,
and keeping them local means a test file reads without jumping elsewhere.

#### The one injection seam

`test_a_failed_extraction_is_not_fatal`
([`test_matching.py:91`](backend/tests/test_matching.py#L91)) passes a local
closure that always returns `None` as `extractor=`, proving a failed extraction
blocks nobody rather than crashing the run. This is the only place production
behaviour is substituted; everywhere else the tests build data.

#### Style

Behaviour-focused throughout. Test names are sentences
(`test_a_disagreement_is_not_the_same_as_a_skip`), and docstrings state the
business reason rather than the code path — for example:

> The optional-question rule needs "no score" told apart from "zero".

No snapshot or golden-file tests. Assertions check observable outputs — HTTP
bodies, returned dicts — never call counts or private state.

### `make_synthetic.py` — 756 lines

Generates the two synthetic cohorts committed at the repo root. Run with:

```bash
uv run python tests/fixtures/make_synthetic.py     # from backend/
```

It exists because **the real sample is too small and too tidy** to reach several
code paths: mentor slots outnumber mentees so nobody is ever waitlisted, almost
nobody answers the avoid question so the constraint never fires, and two
semantic questions calibrate over as few as four pairs.

Two cohorts with deliberately opposite shapes:

```python
COHORTS = (
    {"tag": "A", "seed": 4242, MENTOR: 16, MENTEE: 48},
    {"tag": "B", "seed": 9317, MENTOR: 24, MENTEE: 18},
)
```

A is oversubscribed (waitlist reachable); B is undersubscribed (unused mentors
reachable). Fixed per-cohort seeds mean regenerating one leaves the other
byte-identical.

Two design points worth borrowing:

**Headers are read from the questions database, not hard-coded.** Editing a
question flows into both cohorts on the next regeneration instead of silently
breaking the link.

**Edge cases are guaranteed, not hoped for.** `EDGE_CASES` (line 590) is a fixed
table keyed by row position 0-15, written *over* the random rows by `_apply`
(line 670). Values can be literals or callables, so a case can transform what
was generated rather than replace it. Whatever the seed, every cohort's first 16
rows contain a missing name, a missing email, both missing, an email buried in
text, a stated time-zone offset, a comma inside a checkbox write-in, every
optional question skipped at once, smart quotes, and a 150-word answer.

`_duplicate_rows` (line 680) appends four more: a later resubmission that must
replace, an earlier one that must not, and two identically-named people with no
email who must stay two people.

`SILENT_ROWS` (line 667) makes cohort B's mentees skip one optional question
entirely, so that row has no distribution to calibrate against and drops out.

**Neither cohort is an evaluation set** — free text is sampled from curated
pools, so it says nothing about real match quality. Judge that on the real
sample. See [`backend/tests/fixtures/README.md`](backend/tests/fixtures/README.md)
for all four cohorts and how to obtain the real one.

### Configuration files

**[`backend/pyproject.toml`](backend/pyproject.toml)** — Python ≥3.12. The
notable entry is a three-package exact pin:

```toml
# Pinned exactly: these three determine the embedding vectors, so a version
# drift here would silently change similarity scores between runs.
"sentence-transformers==5.6.1",
"transformers==5.14.1",
"torch==2.13.0",
```

Everything else uses `>=`. `pythonpath = ["."]` lets tests import `app` with no
install step. `openpyxl` is there as pandas' Excel backend, since `read_export`
accepts `.xlsx`.

**[`frontend/tsconfig.json`](frontend/tsconfig.json)** — bundler resolution,
`noEmit`, `react-jsx`. `erasableSyntaxOnly` forbids TypeScript syntax that can't
be erased by stripping types (no `enum`, no constructor parameter properties),
which is why discriminated unions of string literals are used instead of enums.
See [sharp edges](#6-sharp-edges) regarding `strict`.

**[`frontend/.oxlintrc.json`](frontend/.oxlintrc.json)** — oxlint with two
rules: `react/rules-of-hooks` as an error, and `react/only-export-components`,
which is the rule that motivates `main.tsx` existing separately.

---

## 5. Conventions to keep

### Records, not objects

Every dataclass in the codebase is `@dataclass(frozen=True)`, and most carry the
same phrase in their docstring: *"Plain immutable record, no behavior."*

Thirteen of them — five in `inputs.py`, eight in `matching.py`. `PairScore`
([`matching.py:491`](backend/app/matching.py#L491))
is the **only** exception, with two derived `@property` accessors. When you find
yourself wanting a method on a record, that's the bar to clear.

Mutation happens by copy — `resolve_response` uses `dataclasses.replace` rather
than a method on `Response`.

### Why a function instead of inline code

The question this guide exists to answer. There are **two distinct reasons**, and
they look different in the code.

**Extracted for reuse** — two or more call sites, little or no comment:

| Function | Call sites |
|---|---|
| `_cell` ([`inputs.py:571`](backend/app/inputs.py#L571)) | 4, all inside `build_respondents` |
| `displayed_answer` ([`main.py:165`](backend/app/main.py#L165)) | 4, across two endpoints |
| `_is_na` ([`inputs.py:109`](backend/app/inputs.py#L109)) | 3 |
| `_vocabulary_questions` ([`matching.py:702`](backend/app/matching.py#L702)) | 2 |
| `name_row` ([`main.py:157`](backend/app/main.py#L157)) | 2 |
| `_options_for` ([`inputs.py:687`](backend/app/inputs.py#L687)) | 2 |

**Extracted for naming** — exactly one call site, carrying a substantial
docstring that explains reasoning which needed somewhere to live:

| Function | Why it has a name |
|---|---|
| `build_slots` ([`matching.py:863`](backend/app/matching.py#L863)) | Body is one comprehension; docstring is twelve lines on the spare-mentor cap |
| `build_matrix` ([`matching.py:884`](backend/app/matching.py#L884)) | Padding and tie-break jitter, both non-obvious |
| `_points_for` ([`matching.py:513`](backend/app/matching.py#L513)) | Separates *routing* from *accumulating* so `score_pair`'s loop stays readable |
| `_flags` ([`main.py:97`](backend/app/main.py#L97)) | Names the concept "review flags" independently of its one caller |
| `_read_upload` ([`main.py:69`](backend/app/main.py#L69)) | Hides `io.BytesIO(upload.file.read())` boilerplate |

A third, smaller category: **one-line predicates promoted because the concept
deserves a name.** `is_blank` is `normalize(x) == ""`; `missing_email` is
`not _extract_email(...)`. Both could be inlined everywhere. Both stay because
"blank" and "unreadable address" are domain vocabulary — and `missing_email`
additionally keeps `main.py` from reaching for a private helper in `inputs.py`.

If you're adding code and it doesn't fit any of these three, inline it.

### Private and public track the import boundary

The leading underscore signals *"nothing outside this module should call this,"*
not *"this is only used once."* Several public functions have exactly one call
site in the entire codebase (`missing_email`, `resolve_offset`); several private
ones have four (`_cell`).

In `main.py`, which nothing imports, the split means something slightly
different: `_read_upload`, `_require` and `_flags` are FastAPI plumbing, while
the un-prefixed names are response-shaping logic that happens to live nearby.

### Comments explain why, not what

This is the dominant documentation form and the strongest signature of the
codebase's style. Nearly every non-obvious decision has a comment giving the
reason, not a restatement of the code. The CSS drag-and-drop cluster and
`build_slots`'s docstring are the clearest examples.

When you change something these comments describe, change the comment. Several
of them encode browser behaviour or scoring rules that are genuinely hard to
rediscover.

### One dependency-injection seam, and only one

`Extractor` ([`matching.py:649`](backend/app/matching.py#L649)) plus
`extract_avoid_terms(..., extractor=keyword_extractor)` is the whole of it.
Everything else is concretely wired — no strategy objects, no registries, no
plugin points.

Worth stating explicitly so you don't go looking for a pattern that isn't there.

### Frontend: derive, don't store

Store the minimum. `pulled` and `manualPairs` are the only manual state; six
other values are recomputed each render. Two representations of the same fact
can disagree; one cannot.

### Errors are values

`send<T>` never throws. `Result<T>` is a discriminated union, so the type system
requires every caller to handle failure before reading data.

---

## 6. Sharp edges

Real characteristics of the code, with the reason each is acceptable — or the
condition under which it stops being.

**`_session` is one global dict.** ([`main.py:66`](backend/app/main.py#L66))
Every request in the process shares it. No lock, no per-user isolation, no
persistence. Single-tenant by design, and the module docstring argues the trade
openly. It stops being fine the moment two coordinators use one deployment at
the same time — the second upload silently replaces the first's cohort.

**`tsconfig.json` never sets `"strict"`.** So `strictNullChecks` is off, despite
the client leaning heavily on `X | null` state types. **Verified: `npx tsc
--noEmit --strict` passes cleanly on the current code.** Enabling it would be a
one-line change that locks in a property the codebase already has, rather than a
migration. Left as an observation, not a change.

**Manual pairs bypass the avoid constraint.** `blocked` is applied only when
building the solver matrix ([`matching.py:904`](backend/app/matching.py#L904));
the scores dict keeps every pair's true score. So `/api/match` returns a real
percentage for a pair the solver deliberately excluded, and the UI will let you
create it with nothing indicating that. Capacity *is* enforced in manual
matching (the pool only lists mentors with a free place); the avoid constraint
is not. Arguably correct — a manual override is a deliberate act — but it is
silent, and the blocked set is already computed in `run()` if you ever want to
surface it.

**The client reads any bodyless 5xx as "the backend is down."**
([`App.tsx:98`](frontend/src/App.tsx#L98)) FastAPI answers an uncaught exception
with plain-text `Internal Server Error` and no JSON, which is indistinguishable
from the dev proxy's reply when nothing is listening — so a genuine server bug
is reported as an outage and sends you off restarting uvicorn. Upload failures
no longer take this path, since they are now handled 400s, but any *other*
unhandled exception still will. Fixing it properly means giving the app a
handler that returns JSON for 500s, so a missing body genuinely does mean the
proxy.

**`body as T` is a trust boundary, not a validated one.**
([`App.tsx:93`](frontend/src/App.tsx#L93)) The frontend types are hand-maintained
mirrors of the FastAPI responses with no runtime validation and no shared schema.
If a backend response shape changes, TypeScript will not notice — the first sign
will be `undefined` on screen.

**`Results` rebuilds several Maps on every render** with no `useMemo`
([`App.tsx:445-487`](frontend/src/App.tsx#L445-L487)). At cohort scale — tens of
people — this is genuinely free, and memoising would add a dependency array to
keep correct. Named here so nobody assumes it was overlooked.

**`nearest_option` snapping is semantically unreliable.** Write-ins are matched
by embedding similarity, and the result is sometimes wrong in ways a human would
not be: "Blunt is fine, do not cushion it" has been observed snapping to "Both,
depending on the situation". Known behaviour, not a regression. It affects which
listed option a write-in counts as, and the write-in penalty applies regardless.

**Backend changes need a uvicorn restart.** Repeated for emphasis — it has
produced false conclusions in this repo more than once.

---

## Where to start reading

If you want to change scoring, read `matching.py` sections 1-2 and
`test_scoring.py` together — the tests are the clearest statement of the rules.

If you want to change how answers are read, read `inputs.py` sections 2-3 and
`test_inputs.py`.

If you want to change the UI, read `App.tsx` from line 372 down, and
[section 4 above](#apptsx--726-lines) on the derivation.

If you want to add a question, you probably don't need to touch Python at all —
add a row to
[`Mentee_Mentor Questions Database.csv`](Mentee_Mentor%20Questions%20Database.csv)
and regenerate the synthetic cohorts.
