# PLAN.md — Mentor/Mentee Matching Tool

## 1. Purpose

Create a simple mentor/mentee matching tool which takes in CSVs of form responses (exported from Google Forms) and matches mentors with prospective mentees based on their question responses. Each question, as well as the associated set of responses, in the mentor questionnaire directly links to a question and set of responses in the mentee questionnaire.

The program itself is a minimalistic, responsive, simple-to-use FastAPI + React web application. Only bare essentials: uploading both of the files, running the matching tool, and getting the results in a leaderboard ranked by the similarity score.

---

## 2. Inputs

### 2.1 Questions database

In the questions database, each row corresponds to a unique question. The columns indicate:

- The type of question response: multiple choice, checkbox (select all that apply), short answer, long answer.
- Whether the question is required or optional.
- The exact mentor and mentee questions, as well as their respective response options.
- The response matching criteria.
- The weight.
- The similarity percentile cutoffs.

Response option conventions:

- Long answer and short answer questions have `{natural language input}` in these cells.
- Multiple choice and checkbox questions have their response options indexed by number, mirroring the order in which they appear in the survey, and separated by semicolon `;`.
- At least one option string in the database itself contains a comma, so option lists must be parsed on semicolons only, and matching must tolerate options whose text contains commas.

Response matching criteria conventions:

- The criteria indicate, for that question, what needs to be met for a match to be deemed "perfect" (10 points), "good" (5 points), and "no match" (0 points).
- For multiple choice questions, the combinations that are 10, 5, or 0 are explicitly laid out (`0: Yes & No`, `5: Yes & Maybe`, etc.). Combinations are matched in any order.
- For checkbox questions, scoring is determined by the number of response options that overlap across mentor/mentee responses.

The "Similarity Percentile Cutoffs" column stores the percentile pair used to derive that question's semantic similarity cutoffs, expressed as a pair such as 85/50, and defaults to 85/50.

The "weight" column indicates how important a particular question pair is, scaling from 1 to 3. This is simply a multiplier. If a mentor/mentee have a 10-point match on a weight=3 question, their compatibility score goes up 30 points.

### 2.2 Google Forms exports

The questions database is a separate file from the two Google Forms exports. Each export is a spreadsheet where every row is one respondent and every column header is the full text of a survey question.

### 2.3 Linking the database to the exports

The tool must first link the questions database to the exports.

- Each database row is linked to a column in each export by matching its "Mentor Question" and "Mentee Question" text against that export's column headers, using the same string normalization described in section 3.
- Column order is never assumed, since reordering a question in the form would silently break the mapping.
- If any expected question cannot be found in its export, the run aborts with a clear error naming the missing question rather than skipping it.

Once this mapping is established, everything else follows from the database row: the mentor and mentee questions are paired by row, and their response options align by index.

---

## 3. String normalization

Comparison between strings from the questions database and strings from the form exports must be performed on normalized forms of both strings. Normalization consists of:

1. NFKC Unicode normalization
2. Casefolding
3. Stripping leading and trailing whitespace
4. Collapsing internal whitespace runs
5. Converting dash and quote variants to ASCII equivalents

This is required because the questions database and the form exports may differ in invisible ways — the database already contains non-breaking hyphens, and smart quotes and trailing spaces are common in exports — and any such difference would otherwise be misread as a write-in and penalize a respondent who selected a listed option.

Original text is retained for display; only the comparison uses normalized forms.

---

## 4. Response handling

### 4.1 Checkbox responses

Google Forms exports checkbox responses as a single cell containing the selected options separated by commas, whereas the database lists options separated by semicolons. Checkbox responses are therefore split on commas and each resulting option normalized and matched against the semicolon-separated option list for that row.

### 4.2 Duplicate submissions

Respondents may submit a form more than once. Submissions are deduplicated by email address, retaining the most recent submission by timestamp.

### 4.3 Write-in "Other" options

There are multiple choice and checkbox questions which have a write-in "Other" option. Since Google Forms doesn't pass "Other" into the sheet export and just passes the response itself:

- A write-in is detected by comparing the exported response against the given options: if it differs from all of them, it is a write-in. This comparison is performed on the normalized forms described in section 3.
- Once detected, the write-in is embedded and compared against all given response options for that question, and is treated as whichever option it has the highest cosine similarity with.
- Any time a mentor, mentee, or both use a written-in option, a penalty of 5 points is subtracted from that question's contribution after the weight multiplier has been applied.

---

## 5. Scoring

### 5.1 Point scale and weight

Each scored question yields 10 points for a perfect match, 5 for a good match, and 0 for no match. The weight column, scaling from 1 to 3, is a multiplier on that result. A 10-point match on a weight=3 question raises the compatibility score by 30 points.

### 5.2 Multiple choice

Score from the combinations explicitly laid out in the response matching criteria for that row, evaluated in any order.

Almost all response options are the same across both surveys, except for the question "When you give/receive feedback do you prefer…", which has slightly differently worded responses. This does not matter, because matching relies on the response indices, which line up.

### 5.3 Checkbox

Score from the number of response options that overlap across the mentor/mentee responses, per that row's criteria.

### 5.4 Semantic similarity

For most natural language responses, mentor/mentee responses are converted into embedding vectors using `All-mpnet-base-v2` and their cosine similarity is computed.

- The model is loaded locally via `sentence-transformers` with the version pinned in the project's dependencies, so vectors do not silently change between runs.
- Every unique response string is embedded exactly once, up front, and the resulting vectors are cached in memory. The pair-scoring loop then performs only dot products on cached vectors rather than re-running the model.
- Without this, a single question with 20 mentors and 60 mentees would trigger 2,400 forward passes to cover 80 unique strings, and across all semantic questions the run would take minutes instead of seconds.

### 5.5 Per-question percentile calibration

Rather than fixed absolute cutoffs, similarity thresholds are calibrated per question at runtime, because cosine values from this model are not on an absolute scale and their range shifts substantially by question — a question where all responses share vocabulary and structure produces uniformly high similarities, while an open-ended one produces uniformly low ones, so a single fixed cutoff would make some questions score 10 for everyone and others 0 for everyone, contributing no variance and nullifying the weight column.

The procedure:

1. For each semantic question independently, compute the cosine similarity for every mentor × mentee combination. With 20 mentors and 60 mentees, that is 1,200 values.
2. Take the two percentiles specified for that question in the "Similarity Percentile Cutoffs" column, expressed as a pair such as 85/50.
3. The similarity value at the 85th percentile of that question's distribution becomes its 10-point cutoff, and the value at the 50th percentile becomes its 5-point cutoff.
4. Pairs at or above the upper cutoff score 10, pairs between the two score 5, and the rest score 0.

The resulting raw cosine values will differ across questions — hobbies might resolve to 0.58 and 0.41 while a tools question resolves to 0.79 and 0.66 — but the meaning is constant, since every question awards full points to the same top fraction of pairs and therefore contributes comparable variance, leaving the weight column as the only thing controlling relative importance.

The CSV stores the policy (the percentile pair, defaulting to 85/50) rather than a fixed similarity value, so a question can be made more or less selective by editing that one cell. The derived cutoffs are computed from whoever actually submitted responses in a given cycle, and are logged per question so they can be inspected and tuned after a real run.

This makes scores relative to the current cohort rather than absolute: a 10 means "top 15% of pairs on this question in this run," not "these two responses are objectively similar." That is the correct frame here, since the goal is ranking within a fixed pool.

### 5.6 Special question — location

Question: "City, State, and Country where you currently reside."

The location question is not scored by semantic similarity, since embeddings treat all city/state/country strings as near-identical regardless of actual distance. Instead:

1. Each response is converted to a UTC offset relative to Pacific Time: an explicitly stated hour difference is parsed directly, otherwise the city or country is resolved against a lookup table.
2. Responses that resolve to neither are flagged for admin review rather than guessed at.
3. The pair is then scored on the absolute difference between the mentor's and mentee's offsets, since scheduling feasibility for a recurring meeting depends on time zone rather than physical distance.
4. Smaller differences score higher: 0 hours earns 10, 1–2 hours earns 5, and 3 or more earns 0.

### 5.7 Special question — topics/industries/backgrounds to avoid

Question: "Are there any topics, industries, or mentor backgrounds you would prefer to avoid?"

This question is not scored by semantic similarity, since comparing two avoid responses to each other would reward pairs who happen to want to avoid the same thing — a meaningless signal. Instead it is handled as a preprocessing step followed by a hard constraint.

Preprocessing, before any pair scoring occurs:

1. Every non-blank response to this question is collected, filtering out common null answers such as "none," "N/A," and "no."
2. Each is passed once through an LLM extraction step that resolves the free text into a set of concrete terms drawn from a controlled vocabulary — the industries, data science sub-domains, and tools that appear across the other questions in both surveys. A mentor writing "I'd rather not work with anyone going into finance or consulting" resolves to `{finance, consulting}`.
3. This is the only step in the pipeline requiring a model call beyond the local embedding model. If the call fails or returns an unparseable result, the response resolves to an empty set and is flagged for admin review rather than blocking the run.
4. Extraction runs once per respondent, not once per pair, so the cost scales with the small number of people who answer the question rather than with the size of the score matrix.
5. Responses that resolve to no recognized terms produce an empty set and have no effect.

During matching:

1. Each party's extracted set is checked for intersection against the other party's stated industry, sub-domains, and tools, in both directions.
2. Because this is exact matching against a closed vocabulary rather than sentence-level similarity, it fires rarely and only on genuine overlap.
3. When it fires, the pair is treated as a hard block and its cell is excluded from the assignment matrix entirely.
4. Blocked pairs and the specific terms that triggered them are listed for review so a coordinator can override the block before solving.

This question contributes no points to the compatibility score.

### 5.8 Weight-0 questions

Some questions are purely for information collection, serve a specific utility, or have no corresponding question in the other form — like first and last name, email, data science program, and "How many mentees would you like to be matched with?". These questions have weight 0, meaning they are not considered in calculating mentor/mentee similarity.

### 5.9 Optional questions

There are some questions which are optional, either for the mentor/mentee or for both. If one party responds to such a question and the other does not, the question is dropped from that pair's scoring entirely — it contributes nothing to the raw score, and its maximum possible points are also removed from that pair's denominator.

This means skipping an optional question costs a pair nothing rather than lowering their achievable ceiling, so pairs are never ranked higher simply for having had more opportunities to earn points. Only when both parties respond does the question's similarity get calculated and contribute to the match.

### 5.10 Normalized compatibility score

Matching runs on the full mentor × mentee score matrix. Every mentor is scored against every prospective mentee, producing a normalized compatibility score for each pair: raw points divided by the maximum points achievable on the questions both parties answered, so that pairs aren't rewarded for happening to answer more optional questions.

Write-in penalties are subtracted from the raw point total before this normalization, which means a pair's normalized score can in principle fall below zero. This is permitted and requires no special handling, since the solver simply maximizes the normalized value.

The normalized score, expressed as a percentage, is the number displayed on the leaderboard and used for all ranking.

---

## 6. Assignment

Rather than assigning greedily from the top of the matrix, the assignment is solved globally to maximize total compatibility across all pairs, using the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`). This avoids the failure mode of sequential picking, where an early high-scoring pair claims a mentor that a later mentee needed far more, dragging down the overall quality of the cohort.

Matrix construction:

- A mentor who indicated they can take two mentees is represented as two columns in the matrix.
- Mentees who cannot be placed are absorbed by dummy columns scored 0.
- Cells for pairs blocked by the avoid constraint are excluded from the matrix entirely.

After solving:

- Because a global optimum can produce a blocking pair — a mentor and mentee who each prefer the other over their assigned partner — the program detects and reports any such pairs after solving, so they can be reviewed by hand.
- The admin can pin or forbid specific pairings and re-solve.
- If mentees outnumber mentor slots, the solver naturally leaves the mentees with the weakest compatibility across the whole mentor pool unassigned; these are reported as a waitlist ordered by their best available score.
- In the opposite case, where mentor slots outnumber mentees, the surplus columns simply go unfilled — a capacity-2 mentor may end up with one mentee or none — which the dummy-row padding handles without special logic.
- Exact ties are broken at random from a fixed seed, so runs are reproducible.

---

## 7. Application

Once all matching is complete, the program outputs a list of mentor + mentee first name, last name pairs, along with their similarity score. At the click of a button, the user can easily open any match and view the responses of the mentor/mentee to verify whether they like the match.

The program is a minimalistic, responsive, simple-to-use FastAPI + React web application. Only bare essentials:

1. Uploading both of the files.
2. Running the matching tool.
3. Getting the results in a leaderboard, ranked based on the similarity score.
4. Indicating which prospective mentees did not get a match.
5. Surfacing any pairs blocked by the avoid-question constraint, along with the terms that triggered the block, so they can be reviewed and overridden before the assignment is finalized.

---

## 8. Implementation steps

### Step 1 — Project setup

- Create a FastAPI + React web application, minimalistic and responsive.
- Add `sentence-transformers` to the project's dependencies with the version pinned, so vectors do not silently change between runs.
- Add `scipy` for `scipy.optimize.linear_sum_assignment`.
- Add the LLM client used by the avoid-question extraction step.
- Define a single fixed random seed used for tie-breaking, so runs are reproducible.

### Step 2 — String normalization utility

- Implement one normalization function used for every comparison between database strings and export strings.
- Apply, in order: NFKC Unicode normalization, casefolding, stripping leading and trailing whitespace, collapsing internal whitespace runs, and converting dash and quote variants to ASCII equivalents.
- Retain original text for display; use normalized forms only for comparison.
- Use this same function for column-header matching, option matching, checkbox option matching, write-in detection, and null-answer filtering.

### Step 3 — Questions database loader

- Read the questions database into one record per row, preserving row order.
- Parse the question response type into multiple choice, checkbox, short answer, or long answer.
- Parse whether the question is required or optional, for the mentor and for the mentee.
- Parse the weight as an integer from 0 to 3.
- Parse the similarity percentile cutoffs into an upper/lower percentile pair, defaulting to 85/50.
- Parse option lists by splitting on semicolons only, never on commas, since at least one option string contains a comma; retain each option's index and text.
- Recognize `{natural language input}` cells and mark those questions as natural language rather than option-based.
- Parse the response matching criteria for multiple choice rows into the explicit 10 / 5 / 0 combinations, treating each combination as unordered.
- Parse the response matching criteria for checkbox rows into the overlap thresholds that determine 10, 5, and 0.
- Route each row to its handler: weight-0 (unscored), multiple choice, checkbox, semantic, location, or avoid.
- Align the mentor and mentee option lists by index, so scoring relies on indices rather than option text.

### Step 4 — Export loading and column linking

- Accept the two Google Forms exports, one mentor and one mentee, each with one row per respondent and full question text as column headers.
- Match each database row's "Mentor Question" text against the mentor export's column headers and its "Mentee Question" text against the mentee export's column headers, using the normalization from Step 2 on both sides.
- Never assume column order, since reordering a question in the form would silently break the mapping.
- Abort the run with a clear error naming the missing question if any expected question cannot be found in its export; do not skip it.
- Record the resulting mapping, so that from here on the mentor and mentee questions are paired by database row and their response options align by index.

### Step 5 — Deduplication and respondent records

- Group each export's submissions by email address.
- Retain only the most recent submission by timestamp for each email address.
- Build one record per surviving respondent holding their first and last name, email, and their response to every linked question.
- Record each mentor's stated number of mentees, taken from "How many mentees would you like to be matched with?".
- Keep the original response text on the record so it can be displayed when a match is opened.

### Step 6 — Response parsing by question type

- For multiple choice questions, normalize the exported cell and match it against that side's option list to recover the selected option index.
- For checkbox questions, split the exported cell on commas, normalize each resulting option, and match each against the semicolon-separated option list for that row to recover the set of selected option indices.
- For natural language questions, retain the response string.
- Treat a blank cell as no response, to be handled by the optional-question rule.
- Mark any option-based response that differs from all given options as a write-in, and carry its original text forward.

### Step 7 — Embedding cache

- Load `All-mpnet-base-v2` locally via `sentence-transformers`.
- Collect every unique response string that will need an embedding, across all semantic questions and all write-ins, before any pair scoring begins.
- Embed each unique string exactly once, up front, and cache the resulting vectors in memory.
- Have the pair-scoring loop perform only dot products on cached vectors, never re-running the model.

### Step 8 — Write-in resolution and penalty

- For each detected write-in, embed the write-in text and compare it against all given response options for that question by cosine similarity.
- Treat the write-in as whichever option it has the highest cosine similarity with, and score the question on that option index.
- Flag that respondent's answer to that question as a write-in.
- When scoring a pair, subtract a penalty of 5 points from that question's contribution after the weight multiplier has been applied, any time the mentor, the mentee, or both used a written-in option.

### Step 9 — Multiple choice and checkbox scorers

- Implement the multiple choice scorer to look up the mentor/mentee option pair in that row's parsed criteria in any order and return 10, 5, or 0.
- Implement the checkbox scorer to count the number of response options that overlap across the mentor and mentee responses and return 10, 5, or 0 per that row's criteria.
- Have both scorers operate on response indices, so the differently worded responses on "When you give/receive feedback do you prefer…" do not affect the result.

### Step 10 — Semantic scorer and percentile calibration

- For each semantic question independently, compute the cosine similarity for every mentor × mentee combination from the cached vectors.
- Read that question's percentile pair from the "Similarity Percentile Cutoffs" column, defaulting to 85/50.
- Take the similarity value at the upper percentile of that question's distribution as its 10-point cutoff and the value at the lower percentile as its 5-point cutoff.
- Score pairs at or above the upper cutoff as 10, pairs between the two cutoffs as 5, and the rest as 0.
- Log the derived cutoffs per question so they can be inspected and tuned after a real run.

### Step 11 — Location scorer

- Route the location question to its own handler rather than to the semantic scorer.
- Parse an explicitly stated hour difference directly from the response where one is given.
- Otherwise resolve the city or country against a lookup table to obtain the UTC offset relative to Pacific Time.
- Flag responses that resolve to neither for admin review rather than guessing at them.
- Score the pair on the absolute difference between the mentor's and mentee's offsets: 0 hours earns 10, 1–2 hours earns 5, and 3 or more earns 0.

### Step 12 — Avoid-question extraction

- Build the controlled vocabulary from the industries, data science sub-domains, and tools that appear across the other questions in both surveys.
- Collect every non-blank response to the avoid question, filtering out common null answers such as "none," "N/A," and "no."
- Pass each remaining response once through the LLM extraction step to resolve the free text into a set of concrete terms drawn from the controlled vocabulary.
- Run extraction once per respondent, not once per pair.
- On a failed call or an unparseable result, resolve the response to an empty set and flag it for admin review rather than blocking the run.
- Resolve responses with no recognized terms to an empty set, which has no effect.
- Run this entire step before any pair scoring occurs.

### Step 13 — Avoid-question hard constraint

- Check each party's extracted set for intersection against the other party's stated industry, sub-domains, and tools, in both directions.
- Use exact matching against the closed vocabulary rather than sentence-level similarity.
- Treat any pair where this fires as a hard block and exclude its cell from the assignment matrix entirely.
- List blocked pairs and the specific terms that triggered them for review, and allow a coordinator to override the block before solving.
- Award no points from this question to any pair's compatibility score.

### Step 14 — Pair scoring

- Score every mentor against every prospective mentee, over the full mentor × mentee matrix.
- For each scored question, add the question's points multiplied by its weight to the raw total, and add its maximum achievable points to that pair's denominator.
- Subtract the 5-point write-in penalty from a question's contribution after the weight multiplier has been applied.
- Drop any question from that pair's scoring entirely where one party responded and the other did not: contribute nothing to the raw score and remove its maximum possible points from that pair's denominator.
- Exclude weight-0 questions from the calculation.
- Subtract write-in penalties from the raw point total before normalization.
- Divide by the maximum points achievable on the questions both parties answered to produce the normalized compatibility score.
- Allow the normalized score to fall below zero without special handling.
- Express the normalized score as a percentage for display and ranking.
- Retain each pair's underlying responses so they can be shown when a match is opened.

### Step 15 — Global assignment

- Build the assignment matrix from the normalized compatibility scores.
- Represent a mentor who indicated they can take two mentees as two columns.
- Absorb mentees who cannot be placed with dummy columns scored 0, and handle surplus mentor slots with dummy-row padding.
- Exclude the cells of avoid-blocked pairs from the matrix.
- Apply any admin pins and forbids before solving.
- Solve globally with `scipy.optimize.linear_sum_assignment` to maximize total compatibility across all pairs.
- Break exact ties at random from the fixed seed so runs are reproducible.
- Re-solve whenever an admin pins a pairing, forbids a pairing, or overrides an avoid block.

### Step 16 — Post-solve reporting

- Detect any blocking pair — a mentor and mentee who each prefer the other over their assigned partner — and report it so it can be reviewed by hand.
- Report mentees left unassigned as a waitlist ordered by their best available score.
- Output the list of mentor + mentee first name, last name pairs along with their similarity score, ranked by that score.
- Include the blocked pairs and their triggering terms, the location responses flagged for admin review, the avoid responses flagged for admin review, and the logged per-question similarity cutoffs.

### Step 17 — FastAPI backend

- Provide an endpoint to upload both of the files.
- Validate on upload that every expected question resolves to a column in its export, returning the naming error from Step 4 on failure.
- Provide an endpoint to run the matching tool and return the results.
- Provide an endpoint to retrieve the mentor's and mentee's responses for a single match.
- Provide endpoints to override an avoid block, pin a pairing, and forbid a pairing, each re-solving and returning updated results.
- Keep the backend to these essentials only.

### Step 18 — React frontend

- Build an upload view for both of the files, surfacing the missing-question error when validation fails.
- Build a control that runs the matching tool.
- Build the leaderboard showing mentor and mentee first name, last name pairs with their similarity score, ranked by that score.
- Give each result a button that opens the match and displays the mentor's and mentee's responses, so the user can verify whether they like the match.
- Show which prospective mentees did not get a match, as a waitlist ordered by best available score.
- Show pairs blocked by the avoid-question constraint along with the terms that triggered the block, with a control to override the block before the assignment is finalized.
- Show the reported blocking pairs, the location responses flagged for admin review, and the avoid responses flagged for admin review.
- Provide controls to pin and forbid pairings, which re-solve and refresh the results.
- Keep the interface minimalistic, responsive, and simple to use, with nothing beyond the above.
