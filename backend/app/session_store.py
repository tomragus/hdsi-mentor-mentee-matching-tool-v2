"""Persists the session to a GCS bucket, so a fresh backend instance can recover a
cohort after Cloud Run recycles the process -- scaling an idle instance to zero, a
redeploy, and a crash all wipe `_session` the same way, and this covers all three.

Gated by the SESSION_BUCKET env var: unset, every function is a no-op, which is what
keeps local development free of any GCP dependency.
"""

import logging
import os
import pickle

logger = logging.getLogger(__name__)

_BUCKET_ENV_VAR = "SESSION_BUCKET"
_BLOB_NAME = "session.pkl"


def _bucket():
    """The configured bucket, or None when persistence is not configured."""
    name = os.environ.get(_BUCKET_ENV_VAR)
    if not name:
        return None
    from google.cloud import storage  # imported here: free when unconfigured

    return storage.Client().bucket(name)


def save_session(session: dict) -> None:
    """Write the whole session to the bucket, replacing whatever was there. Left to
    raise on failure rather than swallowing it -- a save that silently didn't happen
    would make the next restart 409 with no warning that the guarantee had lapsed.
    """
    bucket = _bucket()
    if bucket is None:
        return
    bucket.blob(_BLOB_NAME).upload_from_string(pickle.dumps(session))
    logger.info("persisted session (%d keys)", len(session))


def load_session() -> dict | None:
    """The last persisted session, or None if there isn't one or persistence is off."""
    bucket = _bucket()
    if bucket is None:
        return None
    blob = bucket.blob(_BLOB_NAME)
    if not blob.exists():
        return None
    session = pickle.loads(blob.download_as_bytes())
    logger.info("recovered persisted session (%d keys)", len(session))
    return session


def delete_session() -> None:
    """Remove the persisted session, if persistence is configured and one exists."""
    bucket = _bucket()
    if bucket is None:
        return
    blob = bucket.blob(_BLOB_NAME)
    if blob.exists():
        blob.delete()
