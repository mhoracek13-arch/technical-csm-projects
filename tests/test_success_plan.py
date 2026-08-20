"""Tests for the Success Plan generator.

No test calls `date.today()`. Every test whose result depends on the clock
passes `today` explicitly, because a test that used the real date would pass
now and start failing on its own in November when the example brief's renewal
date goes past — a test that breaks without anyone changing the code is worse
than no test.

Several of the tests below exist because of a mutation-testing pass: the rule
was deliberately broken, and if the suite stayed green the test was missing.
Twenty mutants survived the first version of this file.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from success_plan.analysis import (
    BAND_CRITICAL,
    BAND_HIGH,
    BAND_MEDIUM,
    _escalation_contribution,
    _measurement_contribution,
    _renewal_contribution,
    _stakeholder_contribution,
    LOW_UTILISATION,
    WEIGHTS,
    assess,
    build_milestones,
    days_to_renewal,
    missing_roles,
    recommend_actions,
    renewal_band,
)
from success_plan.cli import main
from success_plan.model import Account, Adoption, BriefError, Objective
from success_plan.render import render_markdown

TODAY = date(2026, 8, 20)
BRIEF = Path(__file__).parent.parent / "success_plan" / "briefs" / "nordic-finance.json"


def account(**overrides) -> Account:
    """A minimal healthy account, overridable per test."""
    base = {
        "name": "Test Corp",
        "arr": 100000,
        "renewal_date": "2027-06-30",
        "adoption": {
            "licences_purchased": 100,
            "licences_active": 90,
            "trend": "growing",
        },
        "stakeholders": [
            {"name": "Eve", "role": "economic_buyer"},
            {"name": "Cara", "role": "champion"},
            {"name": "Tom", "role": "technical_owner"},
        ],
        "objectives": [
            {"statement": "Do the thing", "metric": "things done", "baseline": "5", "target": "50"},
        ],
    }
    base.update(overrides)
    return Account.from_dict(base)


# ==========================================================================
# Brief parsing and validation
# ==========================================================================

def test_the_bundled_brief_parses():
    data = json.loads(BRIEF.read_text(encoding="utf-8"))
    acct = Account.from_dict(data)
    assert acct.name == "Nordic Finance Group"
    assert acct.arr == 95000
    assert acct.renewal_date == date(2026, 11, 30)
    assert len(acct.objectives) == 3
    assert len(acct.stakeholders) == 3


def test_missing_required_field_names_the_field():
    with pytest.raises(BriefError, match="'arr'"):
        Account.from_dict({"name": "X", "renewal_date": "2027-01-01"})


def test_a_bad_date_is_rejected_clearly():
    with pytest.raises(BriefError, match="not a date"):
        Account.from_dict({"name": "X", "arr": 1, "renewal_date": "next Tuesday"})


def test_currency_formatting_in_arr_is_tolerated():
    assert account(arr="$1,250,000").arr == 1250000


def test_an_unknown_role_is_rejected():
    with pytest.raises(BriefError, match="expected one of"):
        account(stakeholders=[{"name": "X", "role": "chief_vibes_officer"}])


def test_more_active_licences_than_purchased_is_rejected():
    """A brief that says 120 of 100 seats are active is a data-entry error."""
    with pytest.raises(BriefError, match="exceeds"):
        account(adoption={"licences_purchased": 100, "licences_active": 120})


def test_top_level_must_be_an_object():
    with pytest.raises(BriefError, match="JSON object"):
        Account.from_dict([1, 2, 3])


# ==========================================================================
# Utilisation: unknown is not the same as zero
# ==========================================================================

def test_no_seat_data_returns_none_not_zero():
    """Regression guard on a distinction that changes the recommendation.

    "We have no seat data" and "nobody is using it" lead to different
    conversations. Returning 0.0 for both would collapse them.
    """
    assert Adoption().utilisation is None
    assert Adoption(licences_purchased=100, licences_active=0).utilisation == 0.0


def test_utilisation_and_idle_seats():
    ad = Adoption(licences_purchased=250, licences_active=128)
    assert ad.utilisation == 51.2
    assert ad.idle_licences == 122


# ==========================================================================
# Renewal date maths
# ==========================================================================

def test_days_to_renewal_and_bands():
    assert days_to_renewal(account(renewal_date="2026-08-30"), TODAY) == 10
    assert days_to_renewal(account(renewal_date="2026-08-10"), TODAY) == -10


@pytest.mark.parametrize(
    "days,expected",
    [(-1, "OVERDUE"), (0, "CRITICAL"), (60, "CRITICAL"), (61, "HIGH"),
     (120, "HIGH"), (121, "MEDIUM"), (180, "MEDIUM"), (181, "LOW")],
)
def test_renewal_bands(days, expected):
    assert renewal_band(days) == expected


def test_an_overdue_renewal_is_reported_not_hidden():
    a = assess(account(renewal_date="2026-07-01"), TODAY)
    assert a.days_to_renewal < 0
    assert a.renewal_band == "OVERDUE"
    assert "passed" in render_markdown(a)


# ==========================================================================
# Objective coverage - the most useful thing this tool finds
# ==========================================================================

def test_an_objective_with_no_metric_is_flagged_unmeasured():
    a = assess(account(objectives=[
        {"statement": "Improve collaboration"},
        {"statement": "Cut reporting time", "metric": "hours", "baseline": "10"},
    ]), TODAY)
    assert [o.statement for o in a.unmeasured_objectives] == ["Improve collaboration"]


def test_a_metric_without_a_baseline_is_flagged_separately():
    a = assess(account(objectives=[
        {"statement": "Grow usage", "metric": "weekly active users", "target": "200"},
    ]), TODAY)
    assert a.unmeasured_objectives == []
    assert len(a.unbaselined_objectives) == 1


def test_objective_properties():
    assert not Objective("x").is_measurable
    assert Objective("x", metric="m").is_measurable
    assert not Objective("x", metric="m").is_baselined
    assert Objective("x", metric="m", baseline="b").is_baselined


def test_no_objectives_at_all_scores_the_full_penalty():
    a = assess(account(objectives=[]), TODAY)
    measurement = next(c for c in a.contributions if c.factor == "Objective measurement")
    assert measurement.points == WEIGHTS["objective_measurement"]
    assert "no business objectives" in measurement.reason


# ==========================================================================
# Stakeholder coverage
# ==========================================================================

def test_all_required_roles_present_means_no_gaps():
    assert missing_roles(account()) == []


def test_missing_roles_are_listed():
    gaps = missing_roles(account(stakeholders=[{"name": "Cara", "role": "champion"}]))
    assert set(gaps) == {"economic_buyer", "technical_owner"}


def test_an_executive_sponsor_counts_as_the_economic_buyer():
    """They hold the budget in practice; demanding both would be noise."""
    gaps = missing_roles(account(stakeholders=[
        {"name": "Eve", "role": "executive_sponsor"},
        {"name": "Cara", "role": "champion"},
        {"name": "Tom", "role": "technical_owner"},
    ]))
    assert gaps == []


def test_negative_sentiment_is_surfaced():
    a = assess(account(stakeholders=[
        {"name": "Sofia", "role": "blocker", "sentiment": "negative"},
    ]), TODAY)
    assert a.negative_stakeholders == ["Sofia"]


# ==========================================================================
# Risk scoring - explainability is the point
# ==========================================================================

def test_a_healthy_account_scores_low():
    a = assess(account(), TODAY)
    assert a.risk_score < 35
    assert a.risk_band == "HEALTHY"


def test_a_bad_account_scores_high():
    a = assess(account(
        renewal_date="2026-09-15",
        adoption={"licences_purchased": 200, "licences_active": 40, "trend": "declining"},
        objectives=[],
        stakeholders=[],
        open_escalations=3,
        sponsor_changed_recently=True,
    ), TODAY)
    assert a.risk_score >= 65
    assert a.risk_band == "HIGH RISK"


def test_every_contribution_carries_a_reason():
    """A score an executive cannot interrogate is a score they will ignore."""
    a = assess(account(), TODAY)
    assert len(a.contributions) == len(WEIGHTS)
    for c in a.contributions:
        assert c.reason, f"{c.factor} has no reasoning attached"
        assert 0 <= c.points <= c.maximum


def test_the_weights_sum_to_one_hundred():
    assert sum(WEIGHTS.values()) == 100


def test_the_worst_possible_account_scores_exactly_one_hundred():
    """Every factor maxed out, which is only reachable with real bad data."""
    a = assess(account(
        renewal_date="2026-08-21",
        adoption={"licences_purchased": 100, "licences_active": 10, "trend": "declining"},
        objectives=[],
        stakeholders=[],
        open_escalations=99,
        sponsor_changed_recently=True,
    ), TODAY)
    assert a.risk_score == 100


def test_absent_data_scores_lower_than_confirmed_bad_data():
    """A deliberate asymmetry, worth pinning down.

    "No seat data" scores half the adoption weight; "10% utilisation and
    declining" scores all of it. Treating the two the same would let a
    lazily-filled brief look as alarming as a genuinely failing account,
    and the remedies are completely different.
    """
    unknown = assess(account(
        renewal_date="2026-08-21", adoption={}, objectives=[], stakeholders=[],
        open_escalations=99, sponsor_changed_recently=True,
    ), TODAY)
    confirmed = assess(account(
        renewal_date="2026-08-21",
        adoption={"licences_purchased": 100, "licences_active": 10, "trend": "declining"},
        objectives=[], stakeholders=[],
        open_escalations=99, sponsor_changed_recently=True,
    ), TODAY)
    assert unknown.risk_score < confirmed.risk_score == 100


def test_missing_seat_data_is_itself_scored_as_risk():
    """You cannot defend value you cannot measure, so absence is not free."""
    a = assess(account(adoption={}), TODAY)
    adoption = next(c for c in a.contributions if c.factor == "Adoption")
    assert adoption.points > 0
    assert "no seat data" in adoption.reason


def test_low_utilisation_drives_the_adoption_score():
    low = assess(account(adoption={
        "licences_purchased": 100,
        "licences_active": int(LOW_UTILISATION) - 10,
        "trend": "flat",
    }), TODAY)
    high = assess(account(adoption={
        "licences_purchased": 100, "licences_active": 95, "trend": "flat",
    }), TODAY)
    low_pts = next(c for c in low.contributions if c.factor == "Adoption").points
    high_pts = next(c for c in high.contributions if c.factor == "Adoption").points
    assert low_pts > high_pts


# ==========================================================================
# Brief completeness - a sparse brief must not look like a healthy account
# ==========================================================================

def test_an_empty_brief_reports_low_confidence():
    a = assess(Account.from_dict({
        "name": "Mystery Ltd", "arr": 0, "renewal_date": "2027-01-01",
    }), TODAY)
    assert a.confidence.startswith("low")


def test_a_full_brief_reports_high_confidence():
    assert assess(account(), TODAY).confidence == "high"


# ==========================================================================
# Milestones
# ==========================================================================

def test_milestones_are_scheduled_backwards_from_renewal():
    a = assess(account(renewal_date="2027-06-30"), TODAY)
    assert [m.due for m in a.milestones] == sorted(m.due for m in a.milestones)
    assert a.milestones[-1].due < date(2027, 6, 30)


def test_passed_milestones_are_flagged_overdue_not_dropped():
    """Hiding a missed exec review would make a late plan look on track."""
    a = assess(account(renewal_date="2026-09-30"), TODAY)
    overdue = [m for m in a.milestones if m.overdue]
    assert overdue, "expected some milestones to be in the past"
    assert len(a.milestones) == 5, "milestones must never be silently dropped"


def test_milestone_count_is_stable_regardless_of_date():
    for renewal in ("2026-08-21", "2027-12-31"):
        assert len(build_milestones(account(renewal_date=renewal), TODAY)) == 5


# ==========================================================================
# Recommended actions
# ==========================================================================

def test_actions_are_sorted_by_priority():
    a = assess(account(
        renewal_date="2026-09-15", objectives=[], stakeholders=[], adoption={},
    ), TODAY)
    order = [x.priority for x in a.actions]
    assert order == sorted(order)


def test_every_action_explains_itself():
    a = assess(account(objectives=[], stakeholders=[]), TODAY)
    assert a.actions
    for x in a.actions:
        assert x.because, f"action '{x.action}' has no reasoning"


def test_a_healthy_account_produces_no_p1_actions():
    a = assess(account(), TODAY)
    assert [x for x in a.actions if x.priority == "P1"] == []


@pytest.mark.parametrize(
    "renewal_date,expected",
    [
        # BAND_HIGH is 120 days. 2026-12-18 is exactly 120 days after TODAY,
        # 2026-12-19 is 121 - so these two rows straddle the boundary the
        # rule actually turns on. Literal dates on purpose: deriving one by
        # dividing an unrelated threshold is both unclear and fragile.
        ("2026-12-18", "P1"),
        ("2026-12-19", "P2"),
    ],
)
def test_economic_buyer_gap_flips_priority_exactly_at_band_high(renewal_date, expected):
    actions = recommend_actions(
        account(renewal_date=renewal_date,
                stakeholders=[{"name": "Cara", "role": "champion"}]), TODAY)
    buyer = next(x for x in actions if "economic buyer" in x.action)
    assert buyer.priority == expected


def test_the_boundary_dates_above_really_are_120_and_121_days():
    """Guard the guard: if this drifts, the test above stops testing anything."""
    assert days_to_renewal(account(renewal_date="2026-12-18"), TODAY) == BAND_HIGH
    assert days_to_renewal(account(renewal_date="2026-12-19"), TODAY) == BAND_HIGH + 1


@pytest.mark.parametrize(
    "renewal_date,expected", [("2026-12-18", "P1"), ("2026-12-19", "P2")],
)
def test_unmeasured_objective_priority_flips_at_the_same_boundary(renewal_date, expected):
    actions = recommend_actions(
        account(renewal_date=renewal_date,
                objectives=[{"statement": "Something vague"}]), TODAY)
    item = next(x for x in actions if "unmeasured objective" in x.action)
    assert item.priority == expected


def test_a_high_severity_risk_without_mitigation_becomes_an_action():
    a = assess(account(risks=[
        {"description": "SSO not signed off", "severity": "high", "owner": "Sofia"},
    ]), TODAY)
    action = next(x for x in a.actions if "SSO not signed off" in x.action)
    assert action.priority == "P1"
    assert action.owner == "Sofia"


def test_a_mitigated_risk_does_not_become_an_action():
    a = assess(account(risks=[
        {"description": "SSO not signed off", "severity": "high",
         "mitigation": "Security review booked for 3 Sept"},
    ]), TODAY)
    assert not any("SSO" in x.action for x in a.actions)


# ==========================================================================
# Rendering
# ==========================================================================

def test_the_document_depends_on_the_input():
    """Same lesson as the meeting parser: prove the output is not canned."""
    one = render_markdown(assess(account(name="Alpha"), TODAY))
    two = render_markdown(assess(account(name="Beta", arr=5), TODAY))
    assert one != two
    assert "Alpha" in one and "Beta" in two


def test_the_document_contains_every_section():
    doc = render_markdown(assess(account(), TODAY))
    for heading in ("Executive summary", "How the risk score is built",
                    "Business objectives", "Stakeholder map", "Adoption",
                    "Risk register", "Plan of record", "Recommended actions"):
        assert f"## {heading}" in doc, f"missing section: {heading}"


def test_the_document_states_what_it_is_not():
    """The honesty is part of the deliverable, so it is worth a test."""
    doc = render_markdown(assess(account(), TODAY))
    assert "Rules-based, not generative" in doc
    assert "does not predict churn" in doc


def test_an_empty_brief_renders_without_crashing():
    doc = render_markdown(assess(Account.from_dict({
        "name": "Sparse Ltd", "arr": 0, "renewal_date": "2027-01-01",
    }), TODAY))
    assert "No objectives recorded" in doc
    assert "No stakeholders recorded" in doc
    assert "No seat data" in doc


# ==========================================================================
# CLI
# ==========================================================================

def test_cli_renders_the_bundled_brief(capsys):
    assert main(["--today", "2026-08-20"]) == 0
    out = capsys.readouterr().out
    assert "Success Plan — Nordic Finance Group" in out


def test_cli_json_output_is_valid(capsys):
    main(["--today", "2026-08-20", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["account"] == "Nordic Finance Group"
    assert payload["days_to_renewal"] == 102
    assert payload["risk_band"] == "HIGH RISK"
    assert payload["actions"]


def test_cli_output_is_reproducible_for_a_fixed_today(capsys):
    main(["--today", "2026-08-20"]); first = capsys.readouterr().out
    main(["--today", "2026-08-20"]); second = capsys.readouterr().out
    assert first == second


def test_cli_writes_to_a_file(tmp_path, capsys):
    target = tmp_path / "plan.md"
    assert main(["--today", "2026-08-20", "--output", str(target)]) == 0
    assert "Nordic Finance Group" in target.read_text(encoding="utf-8")


def test_cli_fail_on_p1_exits_one(capsys):
    assert main(["--today", "2026-08-20", "--fail-on-p1"]) == 1


def test_cli_missing_brief_exits_two(capsys):
    assert main(["/nope/brief.json"]) == 2
    assert "Brief problem" in capsys.readouterr().err


def test_cli_malformed_brief_exits_two(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert main([str(bad)]) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_rejects_a_bad_today():
    with pytest.raises(SystemExit) as excinfo:
        main(["--today", "yesterday"])
    assert excinfo.value.code == 2


# ==========================================================================
# Exact score arithmetic
#
# Added after mutation testing: the suite was asserting bands and totals but
# not the individual point values, so changing 0.6 to 0.9 in the renewal
# weighting left every test green. The README quotes these numbers, and a
# README does not run in CI.
# ==========================================================================

@pytest.mark.parametrize(
    "days,expected_points",
    [(-1, 25), (0, 25), (BAND_CRITICAL, 25), (BAND_CRITICAL + 1, 15),
     (BAND_HIGH, 15), (BAND_HIGH + 1, 7), (BAND_MEDIUM, 7), (BAND_MEDIUM + 1, 0)],
)
def test_renewal_proximity_points_at_every_band(days, expected_points):
    assert _renewal_contribution(days).points == expected_points


def test_partial_objective_coverage_scores_the_exact_share():
    """1 of 3 unmeasured is 5/15 - the figure the root README quotes."""
    acct = account(objectives=[
        {"statement": "A", "metric": "m", "baseline": "b"},
        {"statement": "B", "metric": "m", "baseline": "b"},
        {"statement": "C"},
    ])
    assert _measurement_contribution(acct).points == 5


def test_partial_stakeholder_coverage_scores_the_exact_share():
    """2 of 3 roles missing is 10/15 - also quoted in the README."""
    acct = account(stakeholders=[{"name": "Cara", "role": "champion"}])
    assert _stakeholder_contribution(acct).points == 10


def test_a_finding_never_scores_zero_points():
    """1 unmeasured objective in 21 rounds to 0 without the max(1, ...).

    A stated gap rendering as 0/15 with an empty bar reads as a bug to
    anyone looking closely at the table.
    """
    many = [{"statement": f"O{i}", "metric": "m", "baseline": "b"} for i in range(20)]
    many.append({"statement": "unmeasured one"})
    contribution = _measurement_contribution(account(objectives=many))
    assert contribution.points >= 1
    assert "1 of 21" in contribution.reason


@pytest.mark.parametrize("count,expected", [(0, 0), (1, 5), (2, 10), (3, 10), (99, 10)])
def test_escalations_score_five_points_each_capped_at_ten(count, expected):
    assert _escalation_contribution(account(open_escalations=count)).points == expected


@pytest.mark.parametrize(
    "score,expected",
    [(0, "HEALTHY"), (34, "HEALTHY"), (35, "WATCH"), (64, "WATCH"),
     (65, "HIGH RISK"), (100, "HIGH RISK")],
)
def test_risk_band_boundaries(score, expected):
    """Constructed directly, so the thresholds are pinned rather than implied."""
    from success_plan.analysis import Assessment, Contribution
    a = Assessment(
        account=account(), today=TODAY, days_to_renewal=0, renewal_band="LOW",
        contributions=[Contribution("synthetic", score, 100, "constructed for this test")],
        unmeasured_objectives=[], unbaselined_objectives=[], missing_roles=[],
        negative_stakeholders=[], milestones=[], actions=[],
    )
    assert a.risk_score == score
    assert a.risk_band == expected


# ==========================================================================
# Action priorities
#
# One test that kills eight mutants: without it, an action could be silently
# deleted or re-prioritised and nothing would notice.
# ==========================================================================

def test_the_full_set_of_actions_and_priorities_is_stable():
    acct = Account.from_dict({
        "name": "Everything Wrong Ltd",
        "arr": 50000,
        "renewal_date": "2026-10-01",
        "adoption": {"licences_purchased": 100, "licences_active": 20, "trend": "declining"},
        "stakeholders": [],
        "objectives": [
            {"statement": "No metric here"},
            {"statement": "Metric but no baseline", "metric": "users", "target": "50"},
        ],
        "risks": [{"description": "Unmitigated thing", "severity": "high"}],
        "open_escalations": 1,
        "sponsor_changed_recently": True,
    })
    a = assess(acct, TODAY)
    got = {x.action: x.priority for x in a.actions}

    expected = {
        "Identify and meet the economic buyer": "P1",
        "Agree a metric for 1 unmeasured objective(s)": "P1",
        "Address 80 idle licences": "P1",
        "Investigate the declining usage trend": "P1",
        "Close out 1 open escalation(s) before renewal": "P1",
        "Book an introduction with the new sponsor": "P1",
        "Write a mitigation for: Unmitigated thing": "P1",
        "Recruit a champion inside the account": "P2",
        "Confirm who owns the integration on the customer side": "P2",
        # Renewal 2026-10-01 minus the 120/90/60/45 day milestones all land
        # before TODAY (2026-08-20); only the 15-day one is still ahead.
        "Reschedule 4 overdue milestone(s)": "P2",
        "Capture a baseline for 1 objective(s)": "P3",
    }
    assert got == expected


def test_a_declining_trend_always_produces_its_own_action():
    a = assess(account(adoption={
        "licences_purchased": 100, "licences_active": 95, "trend": "declining",
    }), TODAY)
    assert any("declining usage trend" in x.action for x in a.actions)


def test_missing_seat_data_produces_a_data_pull_action():
    a = assess(account(adoption={}), TODAY)
    assert any("Pull seat and usage data" in x.action for x in a.actions)


# ==========================================================================
# Milestone boundary
# ==========================================================================

def test_a_milestone_due_today_is_not_yet_overdue():
    """Off-by-one guard: `due < today`, not `<=`."""
    from datetime import timedelta
    from success_plan.analysis import MILESTONE_PLAN
    longest = max(offset for offset, _, _ in MILESTONE_PLAN)
    renewal = TODAY + timedelta(days=longest)
    milestones = build_milestones(account(renewal_date=renewal.isoformat()), TODAY)
    due_today = [m for m in milestones if m.due == TODAY]
    assert due_today, "expected one milestone to land exactly on today"
    assert all(not m.overdue for m in due_today)


# ==========================================================================
# Validation gaps found by review
# ==========================================================================

def test_active_licences_with_zero_purchased_is_rejected():
    """50 active of 0 purchased is a typo, and it used to slip through.

    The old guard was `if purchased and active > purchased`, so the
    0-purchased case skipped the check entirely and then rendered as
    "no seat data in the brief" - worse than a rejection.
    """
    with pytest.raises(BriefError, match="exceeds"):
        account(adoption={"licences_purchased": 0, "licences_active": 50})


def test_negative_escalations_are_rejected_not_clamped():
    """Every other numeric field raises; silently turning -7 into 0 hid typos."""
    with pytest.raises(BriefError, match="cannot be negative"):
        account(open_escalations=-7)


def test_fractional_escalations_are_rejected():
    with pytest.raises(BriefError, match="whole number"):
        account(open_escalations=2.9)


def test_a_compact_date_is_rejected_on_every_python_version():
    """`date.fromisoformat` accepts "20270101" from 3.11 but not on 3.10.

    The CI matrix runs both, so using it would make the same brief valid on
    one leg and invalid on the other. strptime behaves identically.
    """
    with pytest.raises(BriefError, match="YYYY-MM-DD"):
        account(renewal_date="20270101")
    with pytest.raises(BriefError, match="YYYY-MM-DD"):
        account(renewal_date="2027-01-01T00:00:00")


def test_an_executive_sponsor_substitution_is_stated_honestly():
    """The reason string must not claim an economic buyer was identified."""
    acct = account(stakeholders=[
        {"name": "Eve", "role": "executive_sponsor"},
        {"name": "Cara", "role": "champion"},
        {"name": "Tom", "role": "technical_owner"},
    ])
    reason = _stakeholder_contribution(acct).reason
    assert "executive sponsor covers the economic buyer" in reason


def test_a_blocker_role_is_surfaced_even_with_neutral_sentiment():
    """"blocker" is a role, not a sentiment - testing it as one was dead code."""
    a = assess(account(stakeholders=[
        {"name": "Sofia", "role": "blocker", "sentiment": "neutral"},
    ]), TODAY)
    assert a.negative_stakeholders == ["Sofia"]


# ==========================================================================
# Brief completeness bands
#
# The last mutant standing: fixtures only ever scored 0/5 or 5/5, so the
# ratio thresholds were never exercised and changing the denominator from 5
# to 4 left the suite green. These cases sit in the middle on purpose.
# ==========================================================================

def _brief_with_signals(count: int) -> Account:
    """Build a brief filling exactly `count` of the five completeness signals.

    The five are: arr, stakeholders, objectives, a measurable objective, and
    seat data. Ordered so each step adds precisely one.
    """
    data: dict = {"name": "Partial Ltd", "arr": 0, "renewal_date": "2027-06-30"}
    if count >= 1:
        data["arr"] = 50000
    if count >= 2:
        data["stakeholders"] = [{"name": "Cara", "role": "champion"}]
    if count >= 3:
        data["objectives"] = [{"statement": "Unmeasured thing"}]
    if count >= 4:
        data["objectives"] = [{"statement": "Measured thing", "metric": "hours"}]
    if count >= 5:
        data["adoption"] = {"licences_purchased": 100, "licences_active": 60}
    return Account.from_dict(data)


@pytest.mark.parametrize(
    "signals,expected",
    [(0, "low"), (2, "low"), (3, "partial"), (4, "high"), (5, "high")],
)
def test_brief_completeness_bands(signals, expected):
    confidence = assess(_brief_with_signals(signals), TODAY).confidence
    assert confidence.startswith(expected), f"{signals}/5 signals gave {confidence!r}"


def test_the_signal_helper_really_counts_what_it_claims():
    """Guard the guard: if the helper drifts, the bands above stop meaning anything."""
    two = _brief_with_signals(2)
    assert two.arr and two.stakeholders
    assert not two.objectives and not two.adoption.licences_purchased

    five = _brief_with_signals(5)
    assert five.arr and five.stakeholders and five.objectives
    assert any(o.is_measurable for o in five.objectives)
    assert five.adoption.licences_purchased
