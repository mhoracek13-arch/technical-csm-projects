"""Tests for the data sources.

The Wrike client is tested with a fake `requests.Session` rather than by
calling Wrike. A test that needs credentials and a network is not a test,
it is a manual check that fails in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capacity_guardian.sources import (
    FixtureSource,
    WorkloadSource,
    WrikeAPIError,
    WrikeAuthError,
    WrikeClient,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Stands in for requests.Session, returning queued responses in order."""

    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        return self.responses.pop(0)


# --------------------------------------------------------------------------
# FixtureSource
# --------------------------------------------------------------------------

def test_fixture_source_reads_the_bundled_files():
    fixtures = Path(__file__).parent.parent / "capacity_guardian" / "fixtures"
    source = FixtureSource(fixtures)
    assert len(source.get_contacts()) > 0
    assert len(source.get_tasks()) > 0


def test_fixture_source_accepts_a_bare_list(tmp_path):
    """Hand-written fixtures should not need the full API envelope."""
    (tmp_path / "contacts.json").write_text('[{"id": "u1", "type": "Person"}]')
    assert FixtureSource(tmp_path).get_contacts() == [{"id": "u1", "type": "Person"}]


def test_missing_directory_fails_loudly():
    with pytest.raises(FileNotFoundError):
        FixtureSource("/nope/not/here")


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="contacts.json"):
        FixtureSource(tmp_path).get_contacts()


def test_malformed_json_says_so(tmp_path):
    (tmp_path / "tasks.json").write_text("{ not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        FixtureSource(tmp_path).get_tasks()


def test_fixture_source_satisfies_the_protocol():
    """isinstance only proves the method *names* exist.

    A runtime_checkable Protocol will happily accept a `get_contacts` that
    returns a string, so the shapes are asserted separately rather than
    assumed from the isinstance check.
    """
    fixtures = Path(__file__).parent.parent / "capacity_guardian" / "fixtures"
    source = FixtureSource(fixtures)

    assert isinstance(source, WorkloadSource)
    for records in (source.get_contacts(), source.get_tasks()):
        assert isinstance(records, list)
        assert records and all(isinstance(r, dict) for r in records)


# --------------------------------------------------------------------------
# WrikeClient
# --------------------------------------------------------------------------

def test_missing_token_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("WRIKE_API_TOKEN", raising=False)
    with pytest.raises(WrikeAuthError, match="--offline"):
        WrikeClient()


def test_token_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("WRIKE_API_TOKEN", "env-token")
    client = WrikeClient(session=FakeSession([]))
    assert client.session.headers["Authorization"] == "Bearer env-token"


def test_successful_read_returns_the_data_list():
    session = FakeSession([FakeResponse(payload={"kind": "contacts", "data": [{"id": "u1"}]})])
    client = WrikeClient(token="t", session=session)
    assert client.get_contacts() == [{"id": "u1"}]


def test_every_request_carries_a_timeout():
    """An unbounded call is a script that hangs with nothing to debug."""
    session = FakeSession([FakeResponse(payload={"data": []})])
    WrikeClient(token="t", session=session).get_contacts()
    assert session.calls[0]["timeout"] == 20


def test_pagination_follows_the_next_page_token():
    session = FakeSession([
        FakeResponse(payload={"data": [{"id": "1"}], "nextPageToken": "abc"}),
        FakeResponse(payload={"data": [{"id": "2"}]}),
    ])
    client = WrikeClient(token="t", session=session)
    assert [r["id"] for r in client.get_tasks()] == ["1", "2"]
    assert session.calls[1]["params"]["nextPageToken"] == "abc"


def test_page_two_carries_the_token_and_not_the_original_filters():
    """The token encodes the query; resending the filters with it is rejected."""
    session = FakeSession([
        FakeResponse(payload={"data": [{"id": "1"}], "nextPageToken": "abc"}),
        FakeResponse(payload={"data": [{"id": "2"}]}),
    ])
    WrikeClient(token="t", session=session).get_tasks()

    assert "fields" in session.calls[0]["params"]
    assert "fields" not in session.calls[1]["params"]
    assert "status" not in session.calls[1]["params"]


def test_contacts_is_not_paginated():
    """/contacts returns everything at once and rejects pageSize."""
    session = FakeSession([FakeResponse(payload={"data": [{"id": "u1"}]})])
    WrikeClient(token="t", session=session).get_contacts()
    assert "pageSize" not in session.calls[0]["params"]


def test_a_contacts_page_token_is_ignored():
    """Only /tasks paginates, so a stray token must not trigger a second call."""
    session = FakeSession([
        FakeResponse(payload={"data": [{"id": "u1"}], "nextPageToken": "abc"}),
    ])
    client = WrikeClient(token="t", session=session)
    assert len(client.get_contacts()) == 1
    assert len(session.calls) == 1


def test_a_repeated_page_token_does_not_loop_forever():
    """A misbehaving server should not turn into an infinite loop."""
    session = FakeSession([
        FakeResponse(payload={"data": [{"id": "1"}], "nextPageToken": "same"}),
        FakeResponse(payload={"data": [{"id": "2"}], "nextPageToken": "same"}),
    ])
    client = WrikeClient(token="t", session=session)
    assert len(client.get_tasks()) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_token_raises_auth_error(status):
    session = FakeSession([FakeResponse(status_code=status, payload={})])
    with pytest.raises(WrikeAuthError, match=str(status)):
        WrikeClient(token="bad", session=session).get_contacts()


def test_rate_limit_is_reported_as_such():
    session = FakeSession([FakeResponse(status_code=429, payload={})])
    with pytest.raises(WrikeAPIError, match="429"):
        WrikeClient(token="t", session=session).get_contacts()


def test_server_error_includes_the_status_code():
    session = FakeSession([FakeResponse(status_code=500, text="boom")])
    with pytest.raises(WrikeAPIError, match="500"):
        WrikeClient(token="t", session=session).get_contacts()


def test_non_json_response_is_an_api_error():
    session = FakeSession([FakeResponse(status_code=200, payload=None, text="<html>")])
    with pytest.raises(WrikeAPIError, match="non-JSON"):
        WrikeClient(token="t", session=session).get_contacts()


def test_payload_without_a_data_key_is_an_api_error():
    session = FakeSession([FakeResponse(payload={"kind": "tasks"})])
    with pytest.raises(WrikeAPIError, match="no 'data' key"):
        WrikeClient(token="t", session=session).get_tasks()


def test_task_request_asks_for_effort_allocation():
    """Without this field we have no idea how big each task is."""
    session = FakeSession([FakeResponse(payload={"data": []})])
    WrikeClient(token="t", session=session).get_tasks()
    fields = session.calls[0]["params"]["fields"]
    assert "effortAllocation" in fields
    assert "responsibleIds" in fields
    # `dates` is returned by default; naming a default field in `fields`
    # earns an HTTP 400, so it must not be requested.
    assert "dates" not in fields
