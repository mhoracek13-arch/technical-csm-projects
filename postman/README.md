# Postman collection — Wrike API v4

The exact endpoints `capacity_guardian/sources.py` calls, with the same query
parameters, so the Python client's assumptions can be checked against the live
API by hand.

## Setup

1. Import `wrike-capacity-guardian.postman_collection.json` into Postman.
2. Get a permanent API token from Wrike: **Apps & Integrations → API**.
3. Set the collection variable `wrike_token` to that token.

The token field ships empty. Don't commit it — the repo's
`.gitignore` blocks `credentials.json`, `*.pem` and `*.key`, but the surest
protection is not pasting it into a tracked file in the first place.

A pre-request script fails with a readable message if the token is missing,
rather than letting every request come back 401 and leaving you to guess. It
reads `pm.variables`, not `pm.collectionVariables`, so it sees the token
wherever you put it — collection variable, Postman environment, or newman's
`--env-var`. Using the narrower lookup made the guard fire even when the token
was supplied correctly.

## What's in it

| Request | Purpose |
|---|---|
| **Contacts — active people** | `GET /contacts?deleted=false`. Asserts the `id` and `firstName` fields the capacity model joins on. Also asserts that `pageSize` was *not* sent — this endpoint rejects it. |
| **Tasks — active with effort** | `GET /tasks` with `fields`, `status=Active`, `pageSize`. Asserts `effortAllocation` actually comes back. |
| **Tasks — page two (no filters resent)** | Demonstrates the pagination rule that's easy to get wrong. Skips itself automatically when the previous request reported a single page. |
| **Auth check — bad token returns 401** | A negative test: confirms Wrike really answers 401 rather than an empty 200. |

## Three things the assertions exist to catch

**`effortAllocation` disappearing.** It's the field the entire capacity model
rests on. If Wrike stops returning it, the Python tool doesn't crash — it
silently defaults every task to four hours and produces a plausible, wrong
report. That's the failure mode worth a test.

**`dates` in the `fields` parameter.** It's returned by default, and naming a
default field in `fields` earns an HTTP 400. An earlier version of the Python
client requested it, which would have broken the live path on first contact.
There's an assertion here that the parameter stays absent — reading the parsed
query value, because `getQueryString()` doesn't percent-encode and the obvious
`%22dates%22` check could never have failed.

**Resending filters with a page token.** The token already encodes the original
query; sending `fields` and `status` alongside it is rejected. You'd only hit
this on an account with more than 500 active tasks — which is exactly the
account you'd demo against.

## Running it headless

```bash
npm install -g newman
newman run postman/wrike-capacity-guardian.postman_collection.json \
  --env-var wrike_token="$WRIKE_API_TOKEN"
```

Deliberately not wired into the GitHub Actions workflow: it needs a real token
and a live account, so it would fail on every fork and every PR from anyone
without credentials. CI covers the Python client against a fake session
instead. This collection is for verifying the assumptions by hand when the API
changes.
