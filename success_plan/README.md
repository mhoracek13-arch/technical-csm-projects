# Success Plan generator

An account brief goes in, an executive Success Plan comes out.

```bash
python -m success_plan.cli --today 2026-08-20
python -m success_plan.cli my-account.json --output plan.md
python -m success_plan.cli --format json          # for a dashboard
```

## What it actually computes

The point of this is not template filling — that's a Word document with
placeholders. These are the findings it derives that a human reliably misses
when writing a plan by hand:

| Finding | Why it matters |
|---|---|
| **Objectives with no metric** | An objective nobody agreed a measure for cannot be evidenced at renewal. This is the most common gap in real Success Plans and the easiest to miss, because the objective *sounds* fine. |
| **Metrics with no baseline** | You can hit a target and still be unable to show improvement. Separate finding, separate fix. |
| **Missing stakeholder roles** | "No economic buyer identified, 70 days from renewal" is a sentence that changes someone's week. An executive sponsor counts as the economic buyer; demanding both would be noise. |
| **Risk score, factor by factor** | Six weighted factors summing to 100, each printed with its reasoning. An executive who can't interrogate a score will ignore it. |
| **Milestones scheduled backwards from renewal** | Overdue ones are flagged, not dropped. Hiding a missed exec review makes a late plan look on track. |
| **Brief completeness** | A half-empty brief produces a low risk score that looks like good news. The report says which it is. |

Actions are then derived from those findings — each one printed with the gap
that produced it, so a reader can argue with the reasoning rather than just
the conclusion.

## What it is not

Rules, not generative AI. There is no model here and no API call. Every number
in the output traces to a line in `analysis.py`, and the weights are in a dict
called `WEIGHTS` precisely so you can disagree with them.

Also worth being clear about: it has no opinion on anything absent from the
brief, it doesn't read your CRM, and it doesn't predict churn. It applies a
fixed set of rules to what you wrote down and shows its working.

## One deliberate asymmetry

"No seat data in the brief" scores **half** the adoption weight. "10%
utilisation and declining" scores **all** of it.

Treating those the same would let a lazily-filled brief look as alarming as a
genuinely failing account, and the remedies are opposites — one needs a data
pull, the other needs an intervention. `Adoption.utilisation` returns `None`
rather than `0.0` for the same reason, and there's a test pinning it.

## Writing a brief

JSON, so there's no dependency to install. See
[`briefs/nordic-finance.json`](briefs/nordic-finance.json) for a filled-in
example.

Required: `name`, `arr`, `renewal_date`. Everything else is optional — and the
report tells you what its absence cost you.

```json
{
  "name": "Acme Corp",
  "arr": 120000,
  "renewal_date": "2027-03-31",
  "adoption": {
    "licences_purchased": 200,
    "licences_active": 150,
    "trend": "growing"
  },
  "stakeholders": [
    { "name": "Jane Doe", "role": "economic_buyer", "sentiment": "positive" }
  ],
  "objectives": [
    {
      "statement": "Cut reporting effort",
      "metric": "hours per month",
      "baseline": "40",
      "target": "10"
    }
  ]
}
```

Roles: `economic_buyer`, `champion`, `technical_owner`, `executive_sponsor`,
`end_user`, `blocker`. Sentiment: `advocate`, `positive`, `neutral`,
`negative`, `unknown`. Trend: `growing`, `flat`, `declining`, `unknown`.

Bad input is rejected with the field named — including the case where a brief
claims more active licences than were purchased, which is a data-entry error
worth catching rather than scoring.

## Why `--today`

Without it, the same brief produces a different document every day as the
renewal date approaches, which makes the output impossible to test or to diff.
No test in `tests/test_success_plan.py` calls `date.today()`; every test whose
result depends on the clock passes one explicitly. A test using the real date
would pass now and start failing on its own in November.

## Exit codes

`0` fine · `1` P1 actions found and `--fail-on-p1` was set · `2` bad brief ·
`3` could not write the output file.

The `--fail-on-p1` flag exists so this can run as a scheduled job across a book
of accounts and only speak up when something needs a person.

## On the tests

Twenty mutants survived the first version of the test file — rules I could
break in `analysis.py` while the suite stayed green. The renewal-proximity
weights, the per-escalation points, the action priorities and two entire
recommended actions could all be changed or deleted unnoticed.

They're pinned now, verified by re-running the same mutations: 18 of 18 caught,
including a deliberate sanity control. Several tests exist purely because of
that pass, and they say so in their docstrings.
