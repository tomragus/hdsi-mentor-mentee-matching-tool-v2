"""One-time embedding of every string the run needs.

The pair-scoring loop compares the same handful of responses over and over: a
run with 20 mentors and 60 mentees produces 1,200 comparisons per semantic
question but only 80 distinct strings. Embedding inside that loop would mean
thousands of forward passes for work already done.

So every string is collected first, embedded exactly once, and stored as a unit
vector. Scoring then reduces to a dot product.
"""

import logging
from collections.abc import Iterable
from functools import lru_cache

import numpy as np

from app.config import EMBEDDING_MODEL
from app.normalize import normalize
from app.questions import ROLE_SEMANTIC, Question
from app.responses import KIND_BLANK, Response

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_model():
    """Load the sentence-transformer, reusing it for the life of the process."""
    # Imported here rather than at module level: pulling in torch costs seconds,
    # and the API server should not pay that just to serve an upload page.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL)


def collect_texts(
    questions: list[Question], answer_sets: Iterable[dict[int, Response]]
) -> list[str]:
    """Gather every distinct string that will need a vector.

    That is the answers to semantic questions, every write-in, and the listed
    options of any question a write-in appeared on, since resolving a write-in
    means comparing it against those options.
    """
    by_row = {question.row: question for question in questions}
    texts: set[str] = set()
    write_in_rows: set[int] = set()

    for answers in answer_sets:
        for row, response in answers.items():
            question = by_row.get(row)
            if question is None or response.kind == KIND_BLANK:
                continue
            if question.role == ROLE_SEMANTIC:
                texts.add(normalize(response.text))
            for write_in in response.write_ins:
                texts.add(normalize(write_in))
                write_in_rows.add(row)

    for row in write_in_rows:
        question = by_row[row]
        for option in question.mentor_options + question.mentee_options:
            if not option.is_write_in:
                texts.add(normalize(option.text))

    texts.discard("")
    # Sorted so the batch order, and therefore the run, is reproducible.
    return sorted(texts)


def embed(texts: Iterable[str]) -> dict[str, np.ndarray]:
    """Embed each string once, keyed by its normalized form."""
    ordered = list(texts)
    if not ordered:
        return {}

    vectors = load_model().encode(
        ordered,
        # Unit length, so cosine similarity is a plain dot product downstream.
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    logger.info("embedded %d unique responses", len(ordered))
    return dict(zip(ordered, vectors))


def build_cache(
    questions: list[Question], answer_sets: Iterable[dict[int, Response]]
) -> dict[str, np.ndarray]:
    """Collect and embed everything the run needs, before any pair scoring."""
    return embed(collect_texts(questions, answer_sets))


def similarity(cache: dict[str, np.ndarray], left: str, right: str) -> float:
    """Cosine similarity between two responses, from their cached vectors."""
    left_key, right_key = normalize(left), normalize(right)
    if not left_key or not right_key:
        return 0.0

    missing = [key for key in (left_key, right_key) if key not in cache]
    if missing:
        # Only reachable if collection missed a string, which is a bug rather
        # than a data problem, so it should surface loudly.
        raise KeyError(f"no cached embedding for {missing[0]!r}")

    return float(np.dot(cache[left_key], cache[right_key]))
