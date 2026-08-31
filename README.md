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

> **Note:** the backend command has no `--reload`. Backend changes need a manual
> restart, and forgetting has caused real confusion here — the symptom is a UI
> that behaves like an older version of the code.

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
4. Give the build permission to read what you upload and push the built image.
   Projects created since about mid-2024 no longer grant this automatically,
   and without it the first deploy fails — either at `Uploading sources` or,
   less obviously, later at the Artifact Registry push:

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

Deploying from a stale clone is the easiest mistake to make here, and it fails
quietly — you get an older app at a new URL rather than an error.

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

Two of those flags are load-bearing rather than tuning:

- **`--max-instances 1`** is required. Without `SESSION_BUCKET` set (see
  below), the uploaded cohort lives in memory in one process
  ([`main.py`](backend/app/main.py), `_sessions`). A second instance would not
  share it, so a coordinator could upload against one and match against
  another.
- **`--allow-unauthenticated`** is what makes the link openable without a
  Google account. Removing it would make the coordinator sign in.

The first build takes a while — it installs PyTorch and bakes the 418 MB
embedding model into the image so cold starts do not have to download it.

### Redeploying (same URL, existing sessions)

For a code change that isn't a link rotation, deploy to the **same** service
name rather than a fresh random suffix — a new service name means a new URL,
and a visitor's session cookie is scoped to the old one, so it would stop
working for anyone mid-session.

```bash
gcloud run services list   # find the existing service name and region
gcloud run deploy <EXISTING_SERVICE_NAME> \
  --source . \
  --region us-west1 \
  --allow-unauthenticated
```

Leave off `--set-env-vars`, `--memory`, `--max-instances`, etc. — flags you
don't pass are inherited from the service's current revision, so this keeps
`SESSION_BUCKET` and everything else intact. Passing `--set-env-vars` *replaces*
the full env var list rather than adding to it, so it would silently drop
`SESSION_BUCKET` if you forgot to repeat it.

### Persisting sessions across restarts (`SESSION_BUCKET`)

Cloud Run can scale an idle instance to zero, and a redeploy or crash restarts
the process either way — any of the three wipes the in-memory `_sessions` dict
in [`main.py`](backend/app/main.py) the same way. Setting `SESSION_BUCKET` to a
GCS bucket name makes [`session_store.py`](backend/app/session_store.py) write
each visitor's session to a blob keyed by their session cookie, so a fresh
instance can recover it instead of the coordinator having to re-upload.

```bash
gsutil mb -l us-west1 gs://<BUCKET_NAME>
gcloud run services update <SERVICE_NAME> \
  --region us-west1 \
  --update-env-vars SESSION_BUCKET=<BUCKET_NAME>
```

The service's runtime identity needs read/write access to the bucket, and a
lifecycle rule should auto-delete old sessions (this deployment uses a 1-day
rule) so an abandoned cohort's names and emails don't linger indefinitely.
`<RUNTIME_SA>` is the same `${NUMBER}-compute@developer.gserviceaccount.com`
from the one-time setup above, unless you deployed with a custom
`--service-account` — in that case get it with
`gcloud run services describe <SERVICE_NAME> --region us-west1 --format='value(spec.template.spec.serviceAccountName)'`:

```bash
gsutil iam ch serviceAccount:<RUNTIME_SA>:objectAdmin gs://<BUCKET_NAME>
gsutil lifecycle set - gs://<BUCKET_NAME> <<'EOF'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 1}}]}
EOF
```

(`gsutil` is on its way out of the gcloud CLI installer — Google's docs say it
goes away after March 2027 in favor of `gcloud storage`. These commands still
work today; swap in `gcloud storage buckets create` /
`gcloud storage buckets add-iam-policy-binding` / `gcloud storage buckets
update --lifecycle-file=` if `gsutil` has since disappeared for you.)

Left unset, persistence is a no-op and a restart loses every session exactly
as it always has — which is also what happens locally, where `SESSION_BUCKET`
is never set.

### What it costs

Effectively nothing at a few runs per cycle. `--min-instances 0` means the
service sleeps when unused and you are billed only while it is handling a
request, and the free tier is far larger than this workload.

The tradeoff is the **cold start**: the first request after an idle spell takes
roughly 10–30 seconds to load the model. With `SESSION_BUCKET` set, the uploaded
cohort survives that; without it, a page left open across a long idle stretch
will have lost its session, and the app says so ("The server went to sleep...
Upload them again and press Match") rather than showing a confusing error.

If the cold start itself becomes annoying, `--min-instances 1` keeps one
instance warm, for roughly **$15/month**.

### Rotating the link

Deploy under a fresh name, hand out the new URL, then delete the old service:

```bash
gcloud run services delete hsdsc-match-<old-suffix> --region us-west1
```

Everyone's session cookie is scoped to the old URL, so this is also how you'd
deliberately force every visitor to start over.

### If the build fails

**`No matching distribution found for torch==2.13.0`** — the CPU wheel index does
not carry that exact version. Check what is available at
<https://download.pytorch.org/whl/cpu/torch/> and update the pin in both
[`Dockerfile`](Dockerfile) and [`backend/pyproject.toml`](backend/pyproject.toml)
together. Do not let them drift: the comment on that pin explains that these
versions determine the embedding vectors, so changing one silently changes
everyone's compatibility scores.

**The page loads but every button reports the backend is unreachable** — the
frontend build stage failed and `frontend/dist` is missing or stale. Check the
build log for the `npm run build` step.

### Testing the image before deploying (optional)

Requires Docker, which is *not* needed for the deploy path above:

```bash
docker build -t match .
docker run --rm -p 8080:8080 -e PORT=8080 match
# then open http://localhost:8080
```

To prove the model really is baked in rather than fetched at run time, run it
with no network and confirm a match still completes:

```bash
docker run --rm --network none -p 8080:8080 -e PORT=8080 match
```

## Tests

```bash
cd backend  && uv run pytest -q
cd frontend && npm run lint && npm run build
```

Tests that need questionnaire exports **skip** when those files are absent, so a
fresh clone gets a smaller green run rather than a wall of failures.
