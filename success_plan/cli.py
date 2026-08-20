"""Command line entry point for the Success Plan generator.

    python -m success_plan.cli
    python -m success_plan.cli success_plan/briefs/nordic-finance.json
    python -m success_plan.cli my-account.json --output plan.md
    python -m success_plan.cli --format json          # for a dashboard

`--today` exists so the output is reproducible: without it, every run
against the same brief produces a different document as the renewal date
approaches, which makes the tool impossible to test or to diff.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .analysis import assess
from .model import Account, BriefError
from .render import render_markdown

DEFAULT_BRIEF = Path(__file__).parent / "briefs" / "nordic-finance.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="success-plan",
        description=(
            "Turn an account brief into an executive Success Plan. Rules-based: "
            "it scores renewal risk, finds objectives with no metric, flags "
            "missing stakeholder roles and schedules milestones backwards from "
            "the renewal date."
        ),
    )
    parser.add_argument(
        "brief",
        nargs="?",
        default=str(DEFAULT_BRIEF),
        help="JSON account brief. Omit to use the bundled example.",
    )
    parser.add_argument(
        "--today",
        metavar="YYYY-MM-DD",
        help="Treat this as today's date, so output is reproducible.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Document for a human, or JSON for a dashboard (default: markdown).",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write to a file instead of stdout.",
    )
    parser.add_argument(
        "--fail-on-p1",
        action="store_true",
        help="Exit 1 if any P1 action is found. Useful in a scheduled job.",
    )
    return parser


def load_brief(path: Path) -> Account:
    if not path.is_file():
        raise BriefError(f"No such brief: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BriefError(f"{path} is not valid JSON: {exc}")
    return Account.from_dict(payload)


def as_json(assessment) -> str:
    a = assessment
    return json.dumps(
        {
            "account": a.account.name,
            "arr": a.account.arr,
            "renewal_date": a.account.renewal_date.isoformat(),
            "generated_for_date": a.today.isoformat(),
            "days_to_renewal": a.days_to_renewal,
            "renewal_band": a.renewal_band,
            "risk_score": a.risk_score,
            "risk_band": a.risk_band,
            "brief_completeness": a.confidence,
            "score_contributions": [
                {"factor": c.factor, "points": c.points,
                 "maximum": c.maximum, "reason": c.reason}
                for c in a.contributions
            ],
            "missing_roles": a.missing_roles,
            "unmeasured_objectives": [o.statement for o in a.unmeasured_objectives],
            "unbaselined_objectives": [o.statement for o in a.unbaselined_objectives],
            "negative_stakeholders": a.negative_stakeholders,
            "milestones": [
                {"due": m.due.isoformat(), "name": m.name, "overdue": m.overdue}
                for m in a.milestones
            ],
            "actions": [
                {"priority": x.priority, "action": x.action,
                 "because": x.because, "owner": x.owner}
                for x in a.actions
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            parser.error("--today must be in YYYY-MM-DD form")
    else:
        today = date.today()

    try:
        account = load_brief(Path(args.brief))
    except BriefError as exc:
        print(f"Brief problem: {exc}", file=sys.stderr)
        return 2

    assessment = assess(account, today)
    output = as_json(assessment) if args.format == "json" else render_markdown(assessment)

    if args.output:
        try:
            Path(args.output).write_text(output + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Could not write {args.output}: {exc}", file=sys.stderr)
            return 3
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(output)

    if args.fail_on_p1 and any(x.priority == "P1" for x in assessment.actions):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
