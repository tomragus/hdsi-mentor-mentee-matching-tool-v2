# The matching tool as one container: FastAPI serves the API and the built page
# together, so there is a single URL and no CORS.
#
# The repository layout is mirrored inside the image deliberately. config.py finds
# the questions database with Path(__file__).parents[2], and main.py finds the
# built page the same way, so /app/backend/app/ has to sit beside /app/frontend/
# and the CSV exactly as it does in a checkout.

# --- stage 1: build the page --------------------------------------------------
FROM node:22-slim AS web

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# App.tsx reads this with ?raw, from two directories up, so it has to be present
# at the same relative depth the checkout has it at or the build fails.
COPY HOW-MATCHING-WORKS.md /build/HOW-MATCHING-WORKS.md
RUN npm run build

# --- stage 2: the application -------------------------------------------------
FROM python:3.12-slim AS app

# Where the model gets baked in, plus a promise never to reach for the network at
# run time to fetch it.
ENV HF_HOME=/opt/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# CPU-only torch, from PyTorch's own index. The default PyPI wheel drags in the
# entire CUDA stack -- gigabytes of GPU libraries for a machine with no GPU. Same
# version backend/pyproject.toml pins, so the embedding vectors are unchanged.
RUN pip install --no-cache-dir torch==2.13.0 \
      --index-url https://download.pytorch.org/whl/cpu

# The rest, at the versions backend/pyproject.toml pins. torch is already
# satisfied above, so this cannot pull the CUDA build back in.
RUN pip install --no-cache-dir \
      "fastapi>=0.141.1" \
      "numpy>=2.5.1" \
      "openpyxl>=3.1.5" \
      "pandas>=3.0.5" \
      "python-multipart>=0.0.32" \
      "scipy>=1.18.0" \
      "sentence-transformers==5.6.1" \
      "transformers==5.14.1" \
      "uvicorn[standard]>=0.52.1"

WORKDIR /app
COPY backend/app/ ./backend/app/
COPY ["Mentee_Mentor Questions Database.csv", "./"]
COPY --from=web /build/frontend/dist ./frontend/dist

# Bake the 418 MB model into the image rather than fetching it on every cold
# start. Pulled through the app's own loader, so the model name cannot drift from
# EMBEDDING_MODEL in config.py. Offline is lifted just for this step.
RUN HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 PYTHONPATH=/app/backend \
    python -c "from app.inputs import load_model; load_model()"

WORKDIR /app/backend
EXPOSE 8080
# Cloud Run supplies $PORT; the default keeps a plain `docker run` working.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
