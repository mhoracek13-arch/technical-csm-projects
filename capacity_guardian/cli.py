"""Command line entry point for Capacity Guardian.

Run it:

    python -m capacity_guardian.cli --offline
    python -m capacity_guardian.cli --threshold 75 --format json

Everything here is presentation and argument handling. The analysis lives
in `capacity.py` and the data access in `sources.py`, so this file can be
rewritten as a Slack bot or an HTTP endpoint without touching the logic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .capacity import (
    DEFAULT_HOURS_PER_DAY,
    DEFAULT_THRESHOLD,
    DEFAULT_WORK_DAYS,
    CapacityReport,
    build_members,
    plan_reassignments,
)
from .sources import FixtureSource, WrikeAPIError, WrikeAuthError, WrikeClient

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capacity-guardian",
        description=(
            "Flag overallocated team members from Wrike task data and propose "
            "reassignments that do not create a new overload."
        ),
        epilog="Set WRIKE_API_TOKEN for live mode, or pass --offline to use fixtures.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Read bundled JSON fixtures instead of calling the Wrike API.",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=FIXTURE_DIR,
        metavar="DIR",
        help=f"Directory holding contacts.json and tasks.json (default: {FIXTURE_DIR}).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        metavar="PCT",
        help=f"Load percentage above which someone counts as overloaded (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--work-days",
        type=int,
        default=DEFAULT_WORK_DAYS,
        metavar="N",
        help=f"Working days in the analysis window (default: {DEFAULT_WORK_DAYS}).",
    )
    parser.add_argument(
        "--hours-per-day",
        type=float,
        default=DEFAULT_HOURS_PER_DAY,
        metavar="H",
        help=f"Productive hours per person per day (default: {DEFAULT_HOURS_PER_DAY}).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Human-readable report or machine-readable JSON (default: text).",
    )
    return parser


def validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Reject nonsense input early, with a message that says what to do."""
    if not 0 < args.threshold <= 100:
        parser.error("--threshold must be between 0 and 100")
    if args.work_days <= 0:
        parser.error("--work-days must be at least 1")
    if args.hours_per_day <= 0:
        parser.error("--hours-per-day must be greater than 0")


def render_text(report: CapacityReport) -> str:
    lines: list[str] = []
    add = lines.append

    add("CAPACITY GUARDIAN")
    add("=" * 68)
    add(
        f"Window: {report.window_days} working days | "
        f"Overload threshold: {report.threshold:g}% | "
        f"Team size: {len(report.members)}"
    )
    add(
        f"Committed work: {report.total_allocated_hours:g}h | "
        f"Average load: {report.average_load:g}%"
    )
    add("")

    if not report.members:
        add("No team members found. Check that your fixtures or Wrike filters return contacts.")
        return "\n".join(lines)

    add("CURRENT LOAD (after proposed moves)")
    add("-" * 68)
    for member in report.members:
        if not member.is_available:
            marker = "away"  # no capacity this window; not an overload
        elif member.load_percent > report.threshold:
            marker = "OVER"
        else:
            marker = "ok  "
        bar_width = min(int(member.load_percent / 5), 24)
        bar = "#" * bar_width
        add(
            f"  [{marker}] {member.name:<22} {member.load_percent:>5.1f}%  {bar}"
        )
        unestimated = sum(1 for a in member.assignments if not a.estimated)
        if unestimated:
            add(
                f"         note: {unestimated} of {len(member.assignments)} tasks "
                "had no effort estimate (default applied)"
            )
    add("")

    if not report.overloaded:
        add(f"Nobody is above {report.threshold:g}%. No action needed.")
        return "\n".join(lines)

    add(f"OVERLOADED BEFORE REBALANCING ({len(report.overloaded)})")
    add("-" * 68)
    for member in report.overloaded:
        add(f"  {member.name} at {member.load_percent:g}% ({len(member.assignments)} tasks)")
    add("")

    add(f"PROPOSED REASSIGNMENTS ({len(report.reassignments)})")
    add("-" * 68)
    if not report.reassignments:
        add("  None possible - the whole team is at or near capacity.")
    for move in report.reassignments:
        add(f"  Move: {move.task_title} ({move.minutes / 60:g}h)")
        add(
            f"        {move.from_name} -> {move.to_name}  "
            f"[{move.from_name} now {move.from_load_after:g}%, "
            f"{move.to_name} now {move.to_load_after:g}%]"
        )
    add("")

    if report.unresolved:
        add("STILL OVER CAPACITY - ESCALATE")
        add("-" * 68)
        for member in report.unresolved:
            add(
                f"  {member.name} at {member.load_percent:g}%. The team has no "
                "headroom left; this needs scope cuts, a deadline change, or "
                "more people."
            )
    else:
        add("All overloads resolved by the moves above.")

    return "\n".join(lines)


def render_json(report: CapacityReport) -> str:
    payload = {
        "threshold": report.threshold,
        "window_days": report.window_days,
        "average_load_percent": report.average_load,
        "total_allocated_hours": report.total_allocated_hours,
        "members": [
            {
                "name": m.name,
                "user_id": m.user_id,
                "load_percent": m.load_percent,
                "allocated_hours": round(m.allocated_minutes / 60, 1),
                "capacity_hours": round(m.capacity_minutes / 60, 1),
                "task_count": len(m.assignments),
            }
            for m in report.members
        ],
        "reassignments": [
            {
                "task_id": r.task_id,
                "task_title": r.task_title,
                "hours": round(r.minutes / 60, 1),
                "from": r.from_name,
                "to": r.to_name,
                "from_load_after": r.from_load_after,
                "to_load_after": r.to_load_after,
            }
            for r in report.reassignments
        ],
        "unresolved": [m.name for m in report.unresolved],
    }
    return json.dumps(payload, indent=2)


def main(argv: list[str] | None = None) -> int:
    """Return an exit code instead of calling sys.exit, so tests can call this."""
    parser = build_parser()
    args = parser.parse_args(argv)
    validate(args, parser)

    try:
        source = (
            FixtureSource(args.fixtures)
            if args.offline
            else WrikeClient()
        )
        contacts = source.get_contacts()
        tasks = source.get_tasks()
    except WrikeAuthError as exc:
        # Not 2: argparse already exits 2 for usage errors, and a cron job
        # needs to tell "expired token" from "typo in the flag".
        print(f"Authentication problem: {exc}", file=sys.stderr)
        return 5
    except WrikeAPIError as exc:
        print(f"Wrike API problem: {exc}", file=sys.stderr)
        return 3
    except (FileNotFoundError, ValueError) as exc:
        print(f"Fixture problem: {exc}", file=sys.stderr)
        return 4

    members = build_members(
        contacts,
        tasks,
        work_days=args.work_days,
        hours_per_day=args.hours_per_day,
    )
    report = plan_reassignments(
        members,
        threshold=args.threshold,
        window_days=args.work_days,
    )

    output = render_json(report) if args.format == "json" else render_text(report)
    print(output)

    # Non-zero exit when something needs a human. Makes the tool usable in
    # a scheduled job or CI step, not just interactively.
    return 1 if report.unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
