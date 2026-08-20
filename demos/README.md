# Demos

Three small scripts. Each reads real input, has tests, and says in its
docstring what it is and what it isn't.

They're separated from `capacity_guardian/` on purpose. That's a tool you
could run against production data; these are illustrations of an approach.
Mixing the two would make the real one look like a mock.

## meeting_actions.py

Extracts action items, owners and urgency from meeting notes using explicit
rules: sentence splitting, commitment-phrase detection, name matching,
time-word urgency inference.

```bash
python demos/meeting_actions.py                    # bundled sample
python demos/meeting_actions.py notes.txt          # your own file
echo "Bob will send the deck tomorrow" | python demos/meeting_actions.py -
```

```
1. [URGENT] Audit all the Q4 close plans today
   Owner: Alice
   From:  "Alice needs to audit all the Q4 close plans today because the numbers do not reconcile."

2. [URGENT] Escalate it immediately
   Owner: Dana
   From:  "The integration bug is blocking Helix Group, so Dana has to escalate it immediately."
```

Rules, not a model. It will miss indirect phrasing ("can someone look at the
invoice thing?"), has no cross-sentence context, and its name list is
hardcoded rather than pulled from a directory.

Worth reading for two design details. It **cuts the task text at the
commitment marker** rather than just trimming the front, so "the integration
bug is blocking Helix, so Dana has to escalate it immediately" yields
"Escalate it immediately". And it deliberately **does not split on single
newlines**, because pasted notes wrap mid-sentence and the deadline is
usually in the tail.

Name matching is case-sensitive on purpose. Matching case-insensitively meant
"the technical support request" was read as an assignment to the Support
team — a reminder that a looser rule isn't a better one. Both behaviours have
regression tests.

## churn_signals.py

Scores free-text feedback against a weighted phrase lexicon and sorts
accounts worst-first.

```bash
python demos/churn_signals.py                       # bundled sample
python demos/churn_signals.py demos/data/feedback.json
```

Not a churn model. Explicitly:

- **No negation handling.** "not frustrated at all" scores as negative.
- **No intensity.** "slightly annoyed" and "furious" score identically.
- **Hand-built lexicon**, so coverage is whatever was remembered.
- **The score is a sorting key, not a probability.**

The score is signed: positive means risk, negative means health. That matters
— clamping to zero, as an earlier version did, made an enthusiastic customer
and a silent one indistinguishable, which defeats the purpose of scoring
feedback at all.

Two matching subtleties are worth reading `find_signals` for. Phrases are
matched **longest-first across both lexicons**, and matched spans are
**consumed**. Scanning the negative and positive lists separately meant
"not renewing" and "renewing" both fired and cancelled each other out, so the
single clearest churn statement a customer can make came back as `WATCH — log
the friction, address it in the next call`. And without span consumption,
"cancel" matched inside "cancelling" and the account was charged twice. Both
have regression tests.

Why keep a heuristic like this? It runs in milliseconds, needs no training
data, and every score is explainable by pointing at the words that caused it.
If you later train a real model, this is your baseline.

## portfolio_health.py

Summarises ARR by health tier from a CSV and flags healthy high-potential
accounts.

```bash
python demos/portfolio_health.py                     # demos/data/accounts.csv
python demos/portfolio_health.py my-accounts.csv          # your own file
```

Expected columns: `name`, `arr`, `health` (green/yellow/red),
`expansion_potential` (high/medium/low).

The arithmetic is trivial; the parts worth reading are the input validation
and the division guards. Malformed rows are rejected with the row number and
the offending value, which is the difference between a two-second fix and ten
minutes hunting through a spreadsheet. Percentage calculations go through a
`share()` helper that survives a zero denominator — a portfolio of unpaid
pilots is unusual but not impossible, and a `ZeroDivisionError` in front of a
customer is a bad look.

## Tests

```bash
python -m pytest tests/test_demos.py -v
```

The most important one is `test_output_actually_depends_on_the_input`. An
earlier version of the meeting parser accepted a transcript argument, printed
the first 90 characters of it, and then returned a hardcoded list of action
items regardless. That test exists so it can't come back.

Most of the others are regressions too, rather than coverage for its own
sake: splitting rationale on "as" silently deleted the deadline from "send the
report as soon as possible", and `int(float("inf"))` raises `OverflowError`
rather than the `ValueError` the CSV parser was catching. Each test names the
bug it prevents.
