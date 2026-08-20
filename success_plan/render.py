"""Render an Assessment as a Success Plan document.

Markdown, because it pastes into Confluence, Notion, a Wrike description or
an email without fighting anyone, and it diffs cleanly in git.

The section order is deliberate and mirrors how these conversations
actually run: where we stand, what the customer asked for, who decides,
what could go wrong, what happens next. The recommended actions come last
because that is the part the reader is meant to leave with.
"""

from __future__ import annotations

from .analysis import Assessment

BAR_WIDTH = 20


def _bar(points: int, maximum: int) -> str:
    if maximum <= 0:
        return ""
    filled = round(points / maximum * BAR_WIDTH)
    return "#" * filled + "." * (BAR_WIDTH - filled)


def _money(value: int) -> str:
    return f"${value:,}"


def render_markdown(assessment: Assessment) -> str:
    a = assessment
    acct = a.account
    out: list[str] = []
    add = out.append

    # ---------------------------------------------------------------- header
    add(f"# Success Plan — {acct.name}")
    add("")
    add(f"*Generated {a.today.isoformat()} from an account brief. "
        f"Rules-based, not generative — every figure below traces to a rule "
        f"in `success_plan/analysis.py`.*")
    add("")

    # ------------------------------------------------------- executive summary
    add("## Executive summary")
    add("")
    add(f"- **Account:** {acct.name}"
        + (f" ({acct.segment})" if acct.segment else ""))
    add(f"- **ARR:** {_money(acct.arr)}")
    if a.days_to_renewal >= 0:
        add(f"- **Renewal:** {acct.renewal_date.isoformat()} "
            f"— {a.days_to_renewal} days away ({a.renewal_band.lower()} proximity)")
    else:
        add(f"- **Renewal:** {acct.renewal_date.isoformat()} "
            f"— **passed {abs(a.days_to_renewal)} days ago**")
    add(f"- **Risk score:** {a.risk_score}/100 — **{a.risk_band}**")
    add(f"- **Brief completeness:** {a.confidence}")
    if acct.csm:
        add(f"- **CSM:** {acct.csm}")
    add("")

    p1 = [x for x in a.actions if x.priority == "P1"]
    if p1:
        add(f"**{len(p1)} P1 action(s) require attention.** "
            + "; ".join(x.action for x in p1[:3])
            + ("; ..." if len(p1) > 3 else "."))
    else:
        add("**No P1 actions.** The account is on plan against the rules in this tool.")
    add("")

    # ------------------------------------------------------------ risk detail
    add("## How the risk score is built")
    add("")
    add("| Factor | Score | | Reasoning |")
    add("|---|---:|---|---|")
    for c in a.contributions:
        add(f"| {c.factor} | {c.points}/{c.maximum} | `{_bar(c.points, c.maximum)}` | {c.reason} |")
    add(f"| **Total** | **{a.risk_score}/100** | | **{a.risk_band}** |")
    add("")
    add("Higher is worse. Weights are judgement calls, not fitted parameters — "
        "they are listed in `WEIGHTS` so you can argue with them.")
    add("")

    # ------------------------------------------------------------- objectives
    add("## Business objectives")
    add("")
    if not acct.objectives:
        add("> **No objectives recorded.** This is the single biggest gap in the brief — "
            "a Success Plan without stated customer outcomes is an activity list.")
    else:
        add("| Objective | Metric | Baseline | Target | Owner |")
        add("|---|---|---|---|---|")
        for o in acct.objectives:
            metric = o.metric or "**none**"
            baseline = o.baseline or "—"
            target = o.target or "—"
            add(f"| {o.statement} | {metric} | {baseline} | {target} | {o.owner or '—'} |")
        add("")
        if a.unmeasured_objectives:
            add(f"**{len(a.unmeasured_objectives)} objective(s) have no metric.** "
                "These cannot be evidenced at renewal:")
            for o in a.unmeasured_objectives:
                add(f"- {o.statement}")
            add("")
        if a.unbaselined_objectives:
            add(f"**{len(a.unbaselined_objectives)} objective(s) have a metric but no baseline.** "
                "Improvement cannot be shown without a starting point.")
            add("")

    # ----------------------------------------------------------- stakeholders
    add("## Stakeholder map")
    add("")
    if not acct.stakeholders:
        add("> **No stakeholders recorded.** Renewal currently depends on nobody in particular.")
    else:
        add("| Name | Role | Title | Sentiment |")
        add("|---|---|---|---|")
        for s in acct.stakeholders:
            add(f"| {s.name} | {s.role.replace('_', ' ')} | {s.title or '—'} | {s.sentiment} |")
        add("")
    if a.missing_roles:
        pretty = ", ".join(r.replace("_", " ") for r in a.missing_roles)
        add(f"**Coverage gap:** no {pretty} identified.")
        add("")
    if a.negative_stakeholders:
        add(f"**Negative sentiment:** {', '.join(a.negative_stakeholders)}. "
            "Worth a direct conversation before the renewal cycle starts.")
        add("")

    # -------------------------------------------------------------- adoption
    add("## Adoption")
    add("")
    ad = acct.adoption
    utilisation = ad.utilisation
    if utilisation is None:
        add("> **No seat data in the brief.** Adoption cannot be assessed, "
            "which also means value cannot be evidenced.")
    else:
        add(f"- Licences purchased: {ad.licences_purchased}")
        add(f"- Licences active: {ad.licences_active} (**{utilisation:g}% utilisation**)")
        if ad.idle_licences:
            add(f"- Idle licences: {ad.idle_licences}")
        if ad.weekly_active_users:
            add(f"- Weekly active users: {ad.weekly_active_users}")
        add(f"- Trend: {ad.trend}")
    add("")

    # ----------------------------------------------------------------- risks
    add("## Risk register")
    add("")
    if not acct.risks:
        add("No risks recorded in the brief.")
    else:
        add("| Severity | Risk | Mitigation | Owner |")
        add("|---|---|---|---|")
        for r in sorted(acct.risks, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.severity]):
            mitigation = r.mitigation or "**none recorded**"
            add(f"| {r.severity.upper()} | {r.description} | {mitigation} | {r.owner or '—'} |")
    add("")

    # ------------------------------------------------------------ milestones
    add("## Plan of record")
    add("")
    add("Scheduled backwards from the renewal date.")
    add("")
    add("| Due | Milestone | Purpose | Status |")
    add("|---|---|---|---|")
    for m in a.milestones:
        status = "**OVERDUE**" if m.overdue else "upcoming"
        add(f"| {m.due.isoformat()} | {m.name} | {m.purpose} | {status} |")
    add("")

    # --------------------------------------------------------------- actions
    add("## Recommended actions")
    add("")
    if not a.actions:
        add("Nothing outstanding against the rules in this tool.")
    else:
        for x in a.actions:
            add(f"- **{x.priority}** — {x.action}  ")
            add(f"  *Why:* {x.because} · *Owner:* {x.owner}")
        add("")

    if acct.notes:
        add("## Notes")
        add("")
        add(acct.notes)
        add("")

    add("---")
    add("")
    add("*What this tool does not do: it has no opinion on anything absent from "
        "the brief, it does not read your CRM, and it does not predict churn. "
        "It applies a fixed set of rules to what you wrote down and shows its "
        "working.*")

    return "\n".join(out)
