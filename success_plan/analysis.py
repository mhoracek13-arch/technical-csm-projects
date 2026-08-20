"""Analysis over an account brief.

Rules, not a language model. Every number in the output can be traced to a
line in this file, which is the point: a Success Plan gets read by an
executive, and "the tool said so" is not a defensible answer when they ask
why the account is scored 62.

Pure functions throughout - no printing, no file access, no clock. `today`
is always passed in, so the tests are deterministic rather than quietly
breaking every time the renewal date drifts past.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .model import REQUIRED_ROLES, Account, Objective

# Renewal proximity bands, in days. A plan written 200 days out is a
# different document from one written 30 days out.
BAND_CRITICAL = 60
BAND_HIGH = 120
BAND_MEDIUM = 180

# Seat utilisation below this is treated as a commercial problem, not just a
# training one: the customer is paying for capacity they are not using, and
# that is what gets cut at renewal.
LOW_UTILISATION = 60.0

# Risk score weights. Deliberately explicit and summing to 100 so the score
# is explainable line by line rather than being a black box.
WEIGHTS = {
    "renewal_proximity": 25,
    "adoption": 25,
    "objective_measurement": 15,
    "stakeholder_coverage": 15,
    "escalations": 10,
    "sponsor_change": 10,
}

# Milestones are scheduled backwards from the renewal date. Days are
# offsets before renewal.
MILESTONE_PLAN = (
    (120, "Executive business review", "Confirm objectives and exec sponsorship"),
    (90, "Adoption checkpoint", "Review seat utilisation and usage trend"),
    (60, "Value recap delivered", "Written summary of outcomes against objectives"),
    (45, "Renewal proposal issued", "Commercials shared with the economic buyer"),
    (15, "Decision confirmed", "Written commitment or escalation to management"),
)


@dataclass(frozen=True)
class Contribution:
    """One component of the risk score, with its reasoning attached."""

    factor: str
    points: int
    maximum: int
    reason: str


@dataclass(frozen=True)
class Milestone:
    due: date
    name: str
    purpose: str
    overdue: bool


@dataclass(frozen=True)
class Action:
    """A recommended next step, derived from a specific gap."""

    priority: str  # "P1" | "P2" | "P3"
    action: str
    because: str
    owner: str = "CSM"


@dataclass(frozen=True)
class Assessment:
    account: Account
    today: date
    days_to_renewal: int
    renewal_band: str
    contributions: list[Contribution]
    unmeasured_objectives: list[Objective]
    unbaselined_objectives: list[Objective]
    missing_roles: list[str]
    negative_stakeholders: list[str]
    milestones: list[Milestone]
    actions: list[Action]

    @property
    def risk_score(self) -> int:
        # Defensive: every contribution is individually capped and the caps
        # sum to 100, so this clamp should never bind.
        return min(100, sum(c.points for c in self.contributions))

    @property
    def risk_band(self) -> str:
        score = self.risk_score
        if score >= 65:
            return "HIGH RISK"
        if score >= 35:
            return "WATCH"
        return "HEALTHY"

    @property
    def confidence(self) -> str:
        """How much of the brief was actually filled in.

        A plan built from a half-empty brief should say so. Without this, a
        sparse brief produces a low risk score that looks like good news
        when it really means "we know nothing about this account".
        """
        have = 0
        total = 5
        if self.account.objectives:
            have += 1
        if self.account.stakeholders:
            have += 1
        if self.account.adoption.licences_purchased:
            have += 1
        if any(o.is_measurable for o in self.account.objectives):
            have += 1
        if self.account.arr:
            have += 1
        ratio = have / total
        if ratio >= 0.8:
            return "high"
        if ratio >= 0.5:
            return "partial"
        return "low - the brief is missing most of its inputs"


def days_to_renewal(account: Account, today: date) -> int:
    """Negative when the renewal date has already passed."""
    return (account.renewal_date - today).days


def renewal_band(days: int) -> str:
    if days < 0:
        return "OVERDUE"
    if days <= BAND_CRITICAL:
        return "CRITICAL"
    if days <= BAND_HIGH:
        return "HIGH"
    if days <= BAND_MEDIUM:
        return "MEDIUM"
    return "LOW"


def missing_roles(account: Account) -> list[str]:
    present = {s.role for s in account.stakeholders}
    # An executive sponsor satisfies the economic buyer requirement in
    # practice - they are usually the one holding the budget.
    if "executive_sponsor" in present:
        present.add("economic_buyer")
    return [role for role in REQUIRED_ROLES if role not in present]


def _renewal_contribution(days: int) -> Contribution:
    cap = WEIGHTS["renewal_proximity"]
    band = renewal_band(days)
    points = {"OVERDUE": cap, "CRITICAL": cap, "HIGH": int(cap * 0.6),
              "MEDIUM": int(cap * 0.3), "LOW": 0}[band]
    if days < 0:
        reason = f"renewal date passed {abs(days)} days ago"
    else:
        reason = f"{days} days to renewal ({band.lower()} proximity)"
    return Contribution("Renewal proximity", points, cap, reason)


def _adoption_contribution(account: Account) -> Contribution:
    cap = WEIGHTS["adoption"]
    adoption = account.adoption
    utilisation = adoption.utilisation

    if utilisation is None:
        # No data is a risk in itself: you cannot defend value you cannot
        # measure. Scored at half weight rather than zero.
        return Contribution(
            "Adoption", cap // 2, cap,
            "no seat data in the brief, so value cannot be evidenced",
        )

    points = 0
    reasons = []
    if utilisation < LOW_UTILISATION:
        points += int(cap * 0.6)
        reasons.append(
            f"{utilisation:g}% seat utilisation ({adoption.idle_licences} idle licences)"
        )
    if adoption.trend == "declining":
        points += int(cap * 0.4)
        reasons.append("usage trend declining")
    elif adoption.trend == "unknown":
        points += int(cap * 0.2)
        reasons.append("usage trend unknown")

    if not reasons:
        reasons.append(f"{utilisation:g}% seat utilisation, trend {adoption.trend}")
    return Contribution("Adoption", min(points, cap), cap, "; ".join(reasons))


def _measurement_contribution(account: Account) -> Contribution:
    cap = WEIGHTS["objective_measurement"]
    objectives = account.objectives
    if not objectives:
        return Contribution(
            "Objective measurement", cap, cap,
            "no business objectives recorded for this account",
        )
    unmeasured = [o for o in objectives if not o.is_measurable]
    if not unmeasured:
        return Contribution(
            "Objective measurement", 0, cap,
            f"all {len(objectives)} objectives have a metric attached",
        )
    share = len(unmeasured) / len(objectives)
    # max(1, ...): a real finding scoring 0/15 with an empty bar next to it
    # reads as a bug. One unmeasured objective out of 21 rounds to zero.
    return Contribution(
        "Objective measurement", max(1, int(cap * share)), cap,
        f"{len(unmeasured)} of {len(objectives)} objectives have no metric",
    )


def _stakeholder_contribution(account: Account) -> Contribution:
    cap = WEIGHTS["stakeholder_coverage"]
    gaps = missing_roles(account)
    if not account.stakeholders:
        return Contribution(
            "Stakeholder coverage", cap, cap, "no stakeholders identified",
        )
    if not gaps:
        named = {s.role for s in account.stakeholders}
        if "economic_buyer" not in named:
            # missing_roles() lets an executive sponsor stand in for the
            # economic buyer. Saying "economic buyer identified" here would
            # be untrue, and the substitution is documented in the README.
            return Contribution(
                "Stakeholder coverage", 0, cap,
                "executive sponsor covers the economic buyer; "
                "champion and technical owner identified",
            )
        return Contribution(
            "Stakeholder coverage", 0, cap,
            "economic buyer, champion and technical owner all identified",
        )
    share = len(gaps) / len(REQUIRED_ROLES)
    pretty = ", ".join(g.replace("_", " ") for g in gaps)
    return Contribution(
        "Stakeholder coverage", max(1, int(cap * share)), cap,
        f"no {pretty} identified",
    )


def _escalation_contribution(account: Account) -> Contribution:
    cap = WEIGHTS["escalations"]
    count = account.open_escalations
    if count == 0:
        return Contribution("Open escalations", 0, cap, "none open")
    points = min(cap, count * 5)
    return Contribution(
        "Open escalations", points, cap,
        f"{count} open escalation{'s' if count != 1 else ''}",
    )


def _sponsor_contribution(account: Account) -> Contribution:
    cap = WEIGHTS["sponsor_change"]
    if account.sponsor_changed_recently:
        return Contribution(
            "Sponsor change", cap, cap,
            "sponsor changed recently; the relationship needs rebuilding",
        )
    return Contribution("Sponsor change", 0, cap, "sponsor stable")


def build_milestones(account: Account, today: date) -> list[Milestone]:
    """Schedule the plan of record backwards from the renewal date.

    Milestones whose date has already passed are kept and flagged overdue
    rather than dropped. Silently hiding a missed executive business review
    would make a late plan look on track, which is the opposite of useful.
    """
    milestones = []
    for offset, name, purpose in MILESTONE_PLAN:
        due = account.renewal_date - timedelta(days=offset)
        milestones.append(Milestone(due, name, purpose, overdue=due < today))
    return sorted(milestones, key=lambda m: m.due)


def recommend_actions(account: Account, today: date) -> list[Action]:
    """Derive next steps from the gaps actually found in this brief.

    Every action names the finding that produced it, so a reader can
    disagree with the reasoning rather than just the conclusion.
    """
    actions: list[Action] = []
    days = days_to_renewal(account, today)
    gaps = missing_roles(account)

    if "economic_buyer" in gaps:
        priority = "P1" if days <= BAND_HIGH else "P2"
        actions.append(Action(
            priority,
            "Identify and meet the economic buyer",
            f"nobody in the brief holds the budget, {days} days from renewal",
        ))
    if "champion" in gaps:
        actions.append(Action(
            "P2",
            "Recruit a champion inside the account",
            "no internal advocate is named, so renewal depends entirely on the CSM",
        ))
    if "technical_owner" in gaps:
        actions.append(Action(
            "P2",
            "Confirm who owns the integration on the customer side",
            "no technical owner is named; blockers will have no route to resolution",
        ))

    unmeasured = [o for o in account.objectives if not o.is_measurable]
    if unmeasured:
        actions.append(Action(
            "P1" if days <= BAND_HIGH else "P2",
            f"Agree a metric for {len(unmeasured)} unmeasured objective(s)",
            "an objective with no metric cannot be evidenced at renewal",
        ))
    unbaselined = [o for o in account.objectives if o.is_measurable and not o.is_baselined]
    if unbaselined:
        actions.append(Action(
            "P3",
            f"Capture a baseline for {len(unbaselined)} objective(s)",
            "without a starting point, improvement cannot be demonstrated",
        ))
    if not account.objectives:
        actions.append(Action(
            "P1",
            "Run a discovery call to establish business objectives",
            "the brief records no objectives at all",
        ))

    utilisation = account.adoption.utilisation
    if utilisation is None:
        actions.append(Action(
            "P2",
            "Pull seat and usage data for the account",
            "adoption cannot be assessed without it",
        ))
    elif utilisation < LOW_UTILISATION:
        actions.append(Action(
            "P1",
            f"Address {account.adoption.idle_licences} idle licences",
            f"seat utilisation is {utilisation:g}%, which is what gets cut at renewal",
        ))
    if account.adoption.trend == "declining":
        actions.append(Action(
            "P1",
            "Investigate the declining usage trend",
            "declining usage ahead of a renewal is the strongest churn signal available",
        ))

    if account.open_escalations:
        actions.append(Action(
            "P1",
            f"Close out {account.open_escalations} open escalation(s) before renewal",
            "unresolved escalations dominate the renewal conversation",
        ))
    if account.sponsor_changed_recently:
        actions.append(Action(
            "P1",
            "Book an introduction with the new sponsor",
            "a sponsor change resets the relationship and any prior agreement",
        ))
    for risk in account.risks:
        if risk.severity == "high" and not risk.mitigation:
            actions.append(Action(
                "P1",
                f"Write a mitigation for: {risk.description}",
                "the risk is logged as high severity with no mitigation recorded",
                owner=risk.owner or "CSM",
            ))

    overdue = [m for m in build_milestones(account, today) if m.overdue]
    if overdue:
        actions.append(Action(
            "P2",
            f"Reschedule {len(overdue)} overdue milestone(s)",
            "the plan of record has slipped behind the renewal timeline",
        ))

    order = {"P1": 0, "P2": 1, "P3": 2}
    return sorted(actions, key=lambda a: order[a.priority])


def assess(account: Account, today: date) -> Assessment:
    """Run every rule and collect the findings into one object."""
    days = days_to_renewal(account, today)
    contributions = [
        _renewal_contribution(days),
        _adoption_contribution(account),
        _measurement_contribution(account),
        _stakeholder_contribution(account),
        _escalation_contribution(account),
        _sponsor_contribution(account),
    ]
    return Assessment(
        account=account,
        today=today,
        days_to_renewal=days,
        renewal_band=renewal_band(days),
        contributions=contributions,
        unmeasured_objectives=[o for o in account.objectives if not o.is_measurable],
        unbaselined_objectives=[
            o for o in account.objectives if o.is_measurable and not o.is_baselined
        ],
        missing_roles=missing_roles(account),
        negative_stakeholders=[
            # By sentiment or by role: "blocker" is a role (see model.py),
            # never a sentiment, so testing it as a sentiment was dead code.
            s.name for s in account.stakeholders
            if s.sentiment == "negative" or s.role == "blocker"
        ],
        milestones=build_milestones(account, today),
        actions=recommend_actions(account, today),
    )
