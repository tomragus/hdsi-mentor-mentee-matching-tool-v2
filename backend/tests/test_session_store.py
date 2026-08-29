"""Tests for the session persistence module, against a fake bucket rather than real
cloud storage -- these must run without any GCP credentials or network access.
"""

from app.session_store import delete_session, load_session, save_session


def _fake_storage_client(store: dict):
    """A stand-in for google.cloud.storage.Client, backed by a plain dict rather
    than a real bucket.
    """

    class FakeBlob:
        def __init__(self, name: str):
            self.name = name

        def upload_from_string(self, data: bytes) -> None:
            store[self.name] = data

        def exists(self) -> bool:
            return self.name in store

        def download_as_bytes(self) -> bytes:
            return store[self.name]

        def delete(self) -> None:
            del store[self.name]

    class FakeBucket:
        def blob(self, name: str) -> FakeBlob:
            return FakeBlob(name)

    class FakeClient:
        def bucket(self, _name: str) -> FakeBucket:
            return FakeBucket()

    return FakeClient


def test_unconfigured_persistence_is_a_no_op(monkeypatch):
    """SESSION_BUCKET unset is what local development runs under, and it must not
    need GCP credentials to work."""
    monkeypatch.delenv("SESSION_BUCKET", raising=False)

    save_session("token-a", {"anything": True})  # must not raise, and must not reach the network
    assert load_session("token-a") is None
    delete_session("token-a")  # must not raise


def test_a_configured_bucket_round_trips_a_session(monkeypatch):
    monkeypatch.setenv("SESSION_BUCKET", "test-bucket")
    monkeypatch.setattr("google.cloud.storage.Client", _fake_storage_client({}))

    session = {"questions": ["a", "b"], "count": 3}
    save_session("token-a", session)

    assert load_session("token-a") == session


def test_two_tokens_round_trip_to_independent_blobs(monkeypatch):
    """The whole point of keying by token: two visitors' sessions never collide."""
    monkeypatch.setenv("SESSION_BUCKET", "test-bucket")
    monkeypatch.setattr("google.cloud.storage.Client", _fake_storage_client({}))

    save_session("token-a", {"cohort": "a"})
    save_session("token-b", {"cohort": "b"})

    assert load_session("token-a") == {"cohort": "a"}
    assert load_session("token-b") == {"cohort": "b"}

    delete_session("token-a")
    assert load_session("token-a") is None
    assert load_session("token-b") == {"cohort": "b"}, "deleting one token must not touch the other"


def test_deleting_recovers_a_fresh_process_to_the_409_case(monkeypatch):
    """What /api/clear relies on: a deleted session cannot be recovered."""
    monkeypatch.setenv("SESSION_BUCKET", "test-bucket")
    monkeypatch.setattr("google.cloud.storage.Client", _fake_storage_client({}))

    save_session("token-a", {"cohort": "present"})
    delete_session("token-a")

    assert load_session("token-a") is None


def test_deleting_when_nothing_was_ever_saved_does_not_raise(monkeypatch):
    """Clear is reachable before any upload happened, so this has to be safe."""
    monkeypatch.setenv("SESSION_BUCKET", "test-bucket")
    monkeypatch.setattr("google.cloud.storage.Client", _fake_storage_client({}))

    delete_session("token-a")  # must not raise even though the blob never existed
