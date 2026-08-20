# technical-csm-projects

Python tooling for technical customer success work: reading data out of work
management systems, turning it into decisions, and being explicit about which
parts are real analysis and which are heuristics.

**Main project:** [Capacity Guardian](#capacity-guardian) — reads Wrike task
data, finds overallocated teammates, and proposes reassignments that don't
just move the overload onto someone else.

```bash
git clone https://github.com/mhoracek13-arch/technical-csm-projects
cd technical-csm-projects
pip install -r requirements.txt

python -m capacity_guardian.cli --offline   # runs immediately, no credentials
python -m pytest                            # 107 tests, ~0.2s
```

No API token needed to try it. `--offline` reads bundled fixtures and feeds
them through the same *analysis* code as live mode.

---

## Capacity Guardian

**The problem.** Workload is visible per person in Wrike, but nobody looks at
it across a team until someone misses a date or burns out. By then the fix is
expensive.

**What it does.** Pulls active tasks and their effort estimates, computes each
person's committed hours against their available hours for the window, flags
anyone above a threshold, and proposes specific task moves. Anything it
*can't* fix, it escalates rather than hiding.

Real output, copied from a run of the command in the quickstart above:

```
$ python -m capacity_guardian.cli --offline

CAPACITY GUARDIAN
====================================================================
Window: 5 working days | Overload threshold: 80% | Team size: 4
Committed work: 123h | Average load: 76.9%

CURRENT LOAD (after proposed moves)
--------------------------------------------------------------------
  [OVER] Alice Novak             87.5%  #################
  [ok  ] Bob Lindqvist           75.0%  ###############
  [ok  ] Charlie Mensah          75.0%  ###############
         note: 1 of 5 tasks had no effort estimate (default applied)
  [ok  ] Dana Vogel              70.0%  ##############

OVERLOADED BEFORE REBALANCING (2)
--------------------------------------------------------------------
  Alice Novak at 147.5% (4 tasks)
  Dana Vogel at 90% (3 tasks)

PROPOSED REASSIGNMENTS (3)
--------------------------------------------------------------------
  Move: Enterprise migration runbook - Global Logistics NV (20h)
        Alice Novak -> Bob Lindqvist  [Alice Novak now 97.5%, Bob Lindqvist now 75%]
  Move: Refresh onboarding template (4h)
        Alice Novak -> Charlie Mensah  [Alice Novak now 87.5%, Charlie Mensah now 55%]
  Move: Adoption workshop prep (8h)
        Dana Vogel -> Charlie Mensah  [Dana Vogel now 70%, Charlie Mensah now 75%]

STILL OVER CAPACITY - ESCALATE
--------------------------------------------------------------------
  Alice Novak at 87.5%. The team has no headroom left; this needs scope cuts, a deadline change, or more people.
```

Note the last block. Alice starts at 147.5%, three moves bring her to 87.5%,
and the tool says plainly that the remaining 7.5 points can't be solved by
shuffling. Reporting "resolved" there would be the more comfortable output and
the wrong one.

### Usage

```bash
# Offline, using the bundled fixtures
python -m capacity_guardian.cli --offline

# Live against Wrike
export WRIKE_API_TOKEN="your-token"
python -m capacity_guardian.cli

# Tune the model
python -m capacity_guardian.cli --offline --threshold 75 --work-days 10 --hours-per-day 6

# Machine-readable, for a scheduled job or a dashboard
python -m capacity_guardian.cli --offline --format json

# Or install it and use the console script
pip install .
capacity-guardian --offline
```

| Exit code | Meaning |
|---|---|
| 0 | Nothing to do |
| 1 | Unresolved overload — needs a human decision |
| 2 | Bad arguments (argparse owns this one) |
| 3 | Wrike API problem |
| 4 | Bad or missing fixtures |
| 5 | Authentication problem |

Auth deliberately isn't 2: argparse already exits 2 for usage errors, and a
scheduled job needs to tell "expired token" from "typo in the flag".

### How the workload model works

For each person:

```
load % = committed minutes / (working days × hours per day × 60)
```

Committed minutes come from Wrike's `effortAllocation.totalEffort`. Design
decisions worth calling out:

| Decision | Why |
|---|---|
| Unestimated tasks default to 4h, and the report says how many | "Unestimated" is not "free". A model that silently ignores half the book is worse than no model. |
| Multi-assignee tasks split their effort, remainder distributed | Giving each assignee the full estimate double-counts the team. Plain floor division would quietly lose minutes — 100 across 3 people books 99. |
| Zero-capacity people show as `[away]` at 100%, and are excluded from the overload lists | Someone on full leave is unavailable, not idle. Reporting 0% would invite giving them work; reporting them as *overloaded* would be a false alarm that exits non-zero in cron. |
| Reassignments stop 5% short of the threshold | Filling someone to the brim converts one overload into the next one. |
| Collaborators and group records aren't counted as capacity | They don't carry delivery work; including them dilutes the averages. |

### The reassignment algorithm

Greedy, run to a fixed point:

1. Take the most overloaded person who still has movable work.
2. Walk their tasks largest-first; pick the first one that has a legal home.
3. Move it, then **recompute both people's loads**.
4. If none of a person's tasks fit anywhere, mark them exhausted and move on.
5. Anyone still over at the end is reported as unresolved.

Step 3 is where the first version of this script was wrong: it chose one
helper up front and handed them every task without ever updating their
workload, so one person at 45% would absorb unlimited work and still report
45%. `tests/test_capacity.py::test_helper_load_updates_after_each_move` is a
regression test for exactly that.

Step 2 matters too — trying only the largest task and giving up gets you
stuck when a 20h task has no home but the 4h task beside it does.

This is bin packing, so greedy is not optimal. That's a deliberate trade: the
output has to be defensible by a human in a standup, and "move the biggest
thing that fits to whoever has the most room" is explainable in one sentence.
An optimal solver would produce better packing and worse conversations.

### Structure

```
capacity_guardian/
  sources.py    # I/O: Wrike API client + fixture reader behind one Protocol
  capacity.py   # pure logic: no network, no printing, no argparse
  cli.py        # presentation and argument handling
  fixtures/     # synthetic JSON shaped like Wrike payloads
tests/          # 107 tests, none of which touch the network
```

The split is the point. Because `capacity.py` is pure functions over plain
data, it's testable with dicts. Because the API client sits behind a
`Protocol`, offline mode isn't a separate analysis path — it's a different
object satisfying the same contract. Swapping the CLI for a Slack bot would
touch one file.

To be clear about what offline mode does *not* prove: it never exercises the
HTTP layer, so it says nothing about auth, pagination, or whether the query
params are ones Wrike accepts. Those are covered separately in
`tests/test_sources.py` against a fake `requests.Session`.

### Limitations

Stated up front rather than discovered by a reader:

- Effort estimates are only as good as the team's hygiene in Wrike.
- No calendar awareness: holidays, PTO and part-time contracts aren't
  modelled. Capacity is uniform across the team.
- No task dependencies, so it may propose moving something only its current
  owner can do.
- No skills model — "who has room" is not the same as "who can do this".
- Greedy, not optimal.

---

## Demos

`demos/` holds three smaller scripts. They read real input from files or
stdin, they're tested, and each one's docstring states exactly what it is and
what it isn't. See [demos/README.md](demos/README.md).

| Script | What it does | What it isn't |
|---|---|---|
| `meeting_actions.py` | Extracts action items, owners and urgency from meeting notes using explicit rules | Not an LLM. Rules you can read and argue with. |
| `churn_signals.py` | Scores free-text feedback against a weighted phrase lexicon | Not a churn model. No negation or sarcasm handling. |
| `portfolio_health.py` | Summarises ARR by health tier from a CSV, flags expansion targets | Just arithmetic, honestly labelled. |

---

## Testing

```bash
python -m pytest              # all 107
python -m pytest -v           # see every test name
python -m pytest tests/test_capacity.py
```

No test needs a network connection or a credential. The Wrike client is
tested against a fake `requests.Session`; a test that needs a token and a
network isn't a test, it's a manual check that fails in CI.

Several tests exist because a specific bug got shipped once, and a few were
themselves rewritten after mutation testing showed they stayed green while
the code under them was broken — `test_a_move_never_creates_a_new_overload`
used a fixture where no move was ever attempted, so it was asserting the
input. Those stories are in the docstrings.

## Requirements

Python 3.10+, `requests`. `pytest` for the tests. That's it.

## License

MIT — see [LICENSE](LICENSE).
