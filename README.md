# HSDSC Mentor/Mentee Matchmaker

Pairs student mentees with alumni mentors from two Google Form exports.

A coordinator uploads the mentor and mentee questionnaire exports, the tool
scores every possible pairing and solves for the best overall set of matches, and
the coordinator reviews them, adjusts by hand, and exports the result as a CSV.

<img width="1052" height="606" alt="Screenshot 2026-08-11 at 5 01 34 PM" src="https://github.com/user-attachments/assets/ed4fbc53-7cac-4a0f-8d7d-ae886ac90058" />


## Layout

```
Mentee_Mentor Questions Database.csv   the questions, weights and cutoffs
backend/app/
  config.py      constants and text normalization
  inputs.py      read exports, link columns, parse answers, embed
  matching.py    score pairs, apply avoid constraints, solve
  main.py        the HTTP API, and serves the built frontend
frontend/src/
  App.tsx        the whole UI
  index.css      the whole stylesheet
```

Dependencies point one way only:

```
config.py  ←  inputs.py  ←  matching.py  ←  main.py
```

Please keep it that way.

### The questions database is configuration

`Mentee_Mentor Questions Database.csv` is not sample data. It defines every
question, how each one is scored, its weight, and its similarity cutoffs.
Changing the questionnaire usually means editing that file, not the code.

## Running it

Requires **Python 3.12+** with [uv](https://docs.astral.sh/uv/), and **Node 22+**.

Two processes in development:

```bash
cd backend && uv run uvicorn app.main:app       # :8000
cd frontend && npm install && npm run dev       # :5173
```

Open <http://localhost:5173>. Vite proxies `/api` to the backend.

The first run downloads the ~420 MB embedding model.

> **Note:** the backend command has no `--reload`. Backend changes need a manual
> restart, and forgetting has caused real confusion here — the symptom is a UI
> that behaves like an older version of the code.

## Tests

```bash
cd backend  && uv run pytest -q
cd frontend && npm run lint && npm run build
```

Tests that need questionnaire exports **skip** when those files are absent, so a
fresh clone gets a smaller green run rather than a wall of failures.
