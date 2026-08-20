"""Data sources for team workload.

There are two implementations here and they satisfy the same contract:

    WrikeClient    - talks to the live Wrike API v4 (needs WRIKE_API_TOKEN)
    FixtureSource  - reads the same JSON shapes from files on disk

Why bother with two? Because the interesting part of this tool is the
capacity maths, and that part should not care where the data came from.
Coding against an interface (`WorkloadSource` below) means the analysis
code is identical online and offline, the tests never touch the network,
and anyone can clone this repo and run it without credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import requests

API_ROOT = "https://www.wrike.com/api/v4"

# Never leave a network call unbounded. Without a timeout, a hung server
# hangs your script forever with no error to debug.
DEFAULT_TIMEOUT = 20

# Only /tasks paginates. /contacts returns the full set in one response
# and rejects pageSize, so the default is applied per-endpoint, not in _get.
PAGE_SIZE = 500


class WrikeAuthError(RuntimeError):
    """The API token is missing, malformed, or rejected by Wrike."""


class WrikeAPIError(RuntimeError):
    """Wrike returned a response we could not use."""


@runtime_checkable
class WorkloadSource(Protocol):
    """The contract every data source must satisfy.

    A Protocol is Python's way of saying "anything with these methods will
    do" - no inheritance required. `FixtureSource` does not subclass
    anything, but it satisfies `WorkloadSource` because it has methods with
    the right names.

    Worth knowing the limit: `isinstance` against a `runtime_checkable`
    Protocol checks method *names* only. It will happily accept a method
    that returns the wrong type, so the tests assert the shapes separately.
    """

    def get_contacts(self) -> list[dict[str, Any]]:
        """Return Wrike contact records for the people we care about."""
        ...

    def get_tasks(self) -> list[dict[str, Any]]:
        """Return Wrike task records including effort and assignees."""
        ...


class WrikeClient:
    """Read-only client for the Wrike API v4.

    Only implements the two endpoints this tool needs. Deliberately does
    not wrap the whole API - a thin client you fully understand beats a
    thick one you don't.
    """

    def __init__(
        self,
        token: str | None = None,
        api_root: str = API_ROOT,
        timeout: int = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        # Token comes from the environment, never from a literal in the
        # source. This is the single most common way credentials end up
        # committed to a public repo.
        self.token = token or os.environ.get("WRIKE_API_TOKEN")
        if not self.token:
            raise WrikeAuthError(
                "No API token found. Set WRIKE_API_TOKEN in your environment, "
                "or run with --offline to use the bundled fixtures."
            )
        self.api_root = api_root.rstrip("/")
        self.timeout = timeout
        # Reusing one Session keeps the TCP connection alive across calls
        # instead of renegotiating TLS for every request.
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        paginate: bool = False,
    ) -> list[dict[str, Any]]:
        """GET one endpoint and return the data list.

        Wrike replies with ``{"kind": ..., "data": [...], "nextPageToken": ...}``.
        When ``paginate`` is set we keep asking for the next page until the
        token stops coming back.
        """
        url = f"{self.api_root}/{path.lstrip('/')}"
        request_params = dict(params or {})
        if paginate:
            request_params.setdefault("pageSize", PAGE_SIZE)

        collected: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()

        while True:
            try:
                response = self.session.get(url, params=request_params, timeout=self.timeout)
            except requests.Timeout as exc:
                raise WrikeAPIError(f"Wrike timed out after {self.timeout}s on {path}") from exc
            except requests.RequestException as exc:
                raise WrikeAPIError(f"Could not reach Wrike ({path}): {exc}") from exc

            if response.status_code in (401, 403):
                raise WrikeAuthError(
                    "Wrike rejected the token (HTTP "
                    f"{response.status_code}). Check that WRIKE_API_TOKEN is "
                    "current and has read access."
                )
            if response.status_code == 429:
                raise WrikeAPIError(
                    "Rate limited by Wrike (HTTP 429). Wait a moment and retry."
                )
            if not response.ok:
                raise WrikeAPIError(
                    f"Wrike returned HTTP {response.status_code} for {path}: "
                    f"{response.text[:200]}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise WrikeAPIError(f"Wrike sent a non-JSON response for {path}") from exc

            if "data" not in payload:
                raise WrikeAPIError(f"Unexpected payload from {path}: no 'data' key")

            collected.extend(payload["data"])

            token = payload.get("nextPageToken")
            # Guard against a server that keeps handing back the same token,
            # which would otherwise loop forever.
            if not paginate or not token or token in seen_tokens:
                return collected
            seen_tokens.add(token)
            # The token already encodes the original query. Resending the
            # filters alongside it is rejected, so page two onward carries
            # the token and nothing else.
            request_params = {"nextPageToken": token, "pageSize": PAGE_SIZE}

    def get_contacts(self) -> list[dict[str, Any]]:
        # No pagination here: /contacts returns everything at once. Asking
        # Wrike to exclude deleted accounts server-side is cheaper than
        # filtering them out later, though capacity.py does that too.
        return self._get("contacts", params={"deleted": "false"})

    def get_tasks(self) -> list[dict[str, Any]]:
        # `fields` is how you ask Wrike for the optional parts of the task
        # model. Without effortAllocation we have no idea how big a task is.
        # Note that `dates` is NOT listed: it is returned by default, and
        # naming a default field in `fields` earns you an HTTP 400.
        return self._get(
            "tasks",
            params={
                "fields": '["effortAllocation","responsibleIds"]',
                "status": "Active",
            },
            paginate=True,
        )


class FixtureSource:
    """Reads the same shapes from local JSON files.

    Used by ``--offline`` and by the whole test suite. The files are
    hand-written but shaped like real Wrike payloads, so the analysis code
    downstream is identical either way.

    To be clear about what offline mode does not prove: it never exercises
    `_get`, so it says nothing about auth, pagination, or whether the query
    params are ones Wrike accepts. Those are covered by `test_sources.py`
    against a fake Session.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(f"Fixture directory not found: {self.directory}")

    def _load(self, filename: str) -> list[dict[str, Any]]:
        path = self.directory / filename
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Missing fixture file: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc

        # Accept either a full API envelope or a bare list, so hand-written
        # fixtures stay easy to author.
        if isinstance(payload, dict):
            return payload.get("data", [])
        return payload

    def get_contacts(self) -> list[dict[str, Any]]:
        return self._load("contacts.json")

    def get_tasks(self) -> list[dict[str, Any]]:
        return self._load("tasks.json")
