# Deploying

For whoever maintains this. Kelly never reads this file — she gets a link.

The deployed app is **one container**: FastAPI serves the API *and* the built
page, so there is a single URL and no CORS. The link is **unlisted, not
protected** — anyone who has it can use it. That is deliberate. If it ever leaks,
[rotate it](#rotating-the-link).

## One-time setup

1. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install).
2. `gcloud init`, then create a project and **enable billing on it**. Billing is
   required even though this will almost certainly cost nothing.
3. `gcloud services enable run.googleapis.com cloudbuild.googleapis.com`
4. Give the build permission to read what you upload. Projects created since
   about mid-2024 no longer grant this automatically, and without it the very
   first deploy fails at `Uploading sources`:

   ```bash
   PROJECT=$(gcloud config get-value project)
   NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
   gcloud projects add-iam-policy-binding "$PROJECT" \
     --member="serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
     --role="roles/cloudbuild.builds.builder"
   ```

   IAM takes a minute to propagate, so wait before deploying.

You do **not** need Docker installed. `--source .` uploads the repository and
Google builds the image for you.

## Deploying

`--source .` uploads **the directory you are standing in**, so the first job is
making sure that directory holds current code.

From [Cloud Shell](https://shell.cloud.google.com) — where `gcloud` is already
installed and signed in — clone it the first time. **This repository is private**,
so an anonymous `git clone https://…` will fail; sign in to GitHub first with the
`gh` CLI, which Cloud Shell already has:

```bash
gh auth login          # choose GitHub.com > HTTPS > log in with a browser
gh repo clone tomragus/hdsi-mentor-mentee-matching-tool-v2
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
Kelly gets.

Two of those flags are load-bearing rather than tuning:

- **`--max-instances 1`** is required. The uploaded cohort lives in memory in one
  process ([`main.py`](backend/app/main.py), `_session`). A second instance would
  not share it, so a coordinator would upload against one and match against
  another.
- **`--allow-unauthenticated`** is what makes the link openable without a Google
  account. Removing it would make Kelly sign in.

The first build takes a while — it installs PyTorch and bakes the 418 MB
embedding model into the image so cold starts do not have to download it.

## What it costs

Effectively nothing at a few runs per cycle. `--min-instances 0` means the
service sleeps when unused and you are billed only while it is handling a
request, and the free tier is far larger than this workload.

The tradeoff is the **cold start**: the first request after an idle spell takes
roughly 10–30 seconds to load the model, and — more visibly — a page left open
across a long idle stretch will have lost its uploaded cohort. The app now says
so plainly ("The server went to sleep... Upload them again and press Match")
rather than showing a confusing error.

If that becomes annoying, `--min-instances 1` keeps one instance warm and removes
both problems, for roughly **$15/month**.

## Rotating the link

Deploy under a fresh name, hand out the new URL, then delete the old service:

```bash
gcloud run services delete hsdsc-match-<old-suffix> --region us-west1
```

## If the build fails

**`No matching distribution found for torch==2.13.0`** — the CPU wheel index does
not carry that exact version. Check what is available at
<https://download.pytorch.org/whl/cpu/torch/> and update the pin in both
[`Dockerfile`](Dockerfile) and [`backend/pyproject.toml`](backend/pyproject.toml)
together. Do not let them drift: the comment on that pin explains that these
versions determine the embedding vectors, so changing one silently changes
everyone's compatibility scores.

**The page loads but every button reports the backend is unreachable** — the
frontend build stage failed and `frontend/dist` is missing or stale. Check the
build log for the `npm run build` step. Note it needs `HOW-MATCHING-WORKS.md`
copied to the right depth, since `App.tsx` imports it.

## Testing the image before deploying (optional)

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

## Running it locally without a container

Unchanged, and still two processes:

```bash
cd backend && uv run uvicorn app.main:app     # :8000
cd frontend && npm run dev                    # :5173, proxies /api to :8000
```

Note that `uvicorn` now also serves `frontend/dist` at `:8000` if that directory
exists, so `:8000` may show a **stale** build. Use `:5173` while developing.

Backend changes still need a uvicorn restart — there is no reload in that
command, and it has caused real confusion in this project before.
