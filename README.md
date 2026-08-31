# HSDSC Mentor/Mentee Matchmaker

Pairs student mentees with alumni mentors from two Google Form exports.

A coordinator uploads the mentor and mentee questionnaire exports, the tool
scores every possible pairing and solves for the best overall set of matches, and
the coordinator reviews them, adjusts by hand, and exports the result as a CSV.

A reload recovers the report and any manual review edits exactly as they were —
the report from the backend's session (see Deployment below), manual edits from
the browser's own `localStorage`. Only pressing "Clear Session" resets either.

<img width="1052" height="606" alt="Screenshot 2026-08-11 at 5 01 34 PM" src="https://github.com/user-attachments/assets/ed4fbc53-7cac-4a0f-8d7d-ae886ac90058" />

## Get your own copy

Use GitHub's **Fork** button on this repository, then keep the fork private.
The questionnaire exports a coordinator uploads are never committed (see
`.gitignore`), but there's no reason to make an unlisted deployment's setup
details more discoverable than they need to be.

Everything below assumes you're working from your fork, not this one.

## Layout

```
Mentee_Mentor Questions Database.csv   the questions, weights and cutoffs
backend/app/
  config.py         constants and text normalization
  inputs.py         read exports, link columns, parse answers, embed
  matching.py       score pairs, apply avoid constraints, solve
  main.py           the HTTP API, and serves the built frontend
  session_store.py  persists the session to GCS, so a restart doesn't lose it
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

## Running it locally

Requires **Python 3.12+** with [uv](https://docs.astral.sh/uv/), and **Node 22+**.

Two processes in development:

```bash
cd backend && uv run uvicorn app.main:app       # :8000
cd frontend && npm install && npm run dev       # :5173
```

Open <http://localhost:5173>. Vite proxies `/api` to the backend.

The first run downloads the ~420 MB embedding model.

## Deployment

The deployed app is **one container**: FastAPI serves the API *and* the built
page, so there is a single URL and no CORS. The link this produces is
**unlisted, not protected** — anyone who has it can use it. That is
deliberate. If it ever leaks, [rotate it](#rotating-the-link).

### One-time GCP setup

1. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install).
2. `gcloud init`, then create a project and **enable billing on it**. Billing is
   required even though this will almost certainly cost nothing at typical
   usage.
3. `gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com`
4. Give the build permission to read what you upload and push the built image:

   ```bash
   PROJECT=$(gcloud config get-value project)
   NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
   gcloud projects add-iam-policy-binding "$PROJECT" \
     --member="serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
     --role="roles/run.builder"
   ```

   IAM takes a minute to propagate, so wait before deploying.

You do **not** need Docker installed. `--source .` uploads the repository and
Google builds the image for you.

### First deploy

`--source .` uploads **the directory you are standing in**, so the first job is
making sure that directory holds current code.

Do this from the same place you ran the one-time setup above — `gcloud`
tracks its active project per-environment, and running the deploy from
somewhere else (a local install after setting up in Cloud Shell, or vice
versa) reproduces the exact "Uploading sources" failure step 4 was meant to
prevent, just with no obvious cause. Confirm first:

```bash
gcloud config get-value project   # should print the project from setup, above
```

From [Cloud Shell](https://shell.cloud.google.com) — where `gcloud` is already
installed and signed in — clone your fork the first time. If your fork is
private, sign in to GitHub first with the `gh` CLI, which Cloud Shell already
has:

```bash
gh auth login          # choose GitHub.com > HTTPS > log in with a browser
gh repo clone <your-github-username>/hdsi-mentor-mentee-matching-tool-v2
cd hdsi-mentor-mentee-matching-tool-v2
```

`gh auth login` also configures git, so `git pull` works from then on. Cloud
Shell's home directory persists, so this is a one-time step.

and on every deploy after that, pull first:

```bash
cd ~/hdsi-mentor-mentee-matching-tool-v2 && git pull
```

Then pick a service name with a random suffix. The name is part of the URL, and
the URL is the only thing keeping the app private.

```bash
gcloud run deploy hsdsc-match-$(openssl rand -hex 4) \
  --source . \
  --region us-west1 \
  --memory 2Gi \
  --cpu 2 \
  --max-instances 1 \
  --min-instances 0 \
  --timeout 600 \
  --allow-unauthenticated
```

It prints a URL like `https://hsdsc-match-a1b2c3d4-uw.a.run.app`. That is what
you hand the coordinator.

The first build takes a while — it installs PyTorch and bakes the 418 MB
embedding model into the image so cold starts do not have to download it.

### Redeploying (same URL, existing sessions)

For a code change that isn't a link rotation, deploy to the **same** service
name rather than a fresh random suffix.

```bash
gcloud run services list   # find the existing service name and region
gcloud run deploy <EXISTING_SERVICE_NAME> \
  --source . \
  --region us-west1 \
  --allow-unauthenticated
```

### What it costs

Should cost effectively nothing, maybe a cent or two at most for one year's matching. See your billing account's page on Google Cloud to view your costs.
