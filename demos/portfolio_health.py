#!/usr/bin/env python3
"""Portfolio ARR and health summary from a CSV of accounts.

Reads real input from a file rather than hardcoding the accounts, which is
the difference between a script and a screenshot. The arithmetic is
deliberately simple; the parts worth reading are the input validation and
the division guards.

Usage:
    python demos/portfolio_health.py                            # bundled sample
    python demos/portfolio_health.py demos/data/accounts.csv    # explicit path

Expected CSV columns: name, arr, health, expansion_potential
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = {"name", "arr", "health", "expansion_potential"}
VALID_HEALTH = {"green", "yellow", "red"}
VALID_POTENTIAL = {"high", "medium", "low"}
DEFAULT_CSV = Path(__file__).parent / "data" / "accounts.csv"


@dataclass(frozen=True)
class Account:
    name: str
    arr: int
    health: str
    expansion_potential: str


class InputError(Exception):
    """The CSV was missing, malformed, or had unusable values."""


def parse_accounts(path: Path) -> list[Account]:
    """Read and validate the CSV, reporting the row number on failure.

    Naming the offending row is the difference between a two-second fix and
    a ten-minute hunt through a spreadsheet.
    """
    if not path.is_file():
        raise InputError(f"No such file: {path}")

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InputError(f"{path} is empty.")

        headers = {h.strip().lower() for h in reader.fieldnames}
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise InputError(
                f"{path} is missing column(s): {', '.join(sorted(missing))}. "
                f"Found: {', '.join(sorted(headers))}"
            )

        accounts: list[Account] = []
        for line_no, row in enumerate(reader, start=2):  # row 1 is the header
            clean = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}

            name = clean.get("name") or ""
            if not name:
                raise InputError(f"Row {line_no}: 'name' is empty.")

            raw_arr = clean.get("arr", "").replace(",", "").replace("$", "")
            try:
                arr = int(float(raw_arr))
            except (ValueError, OverflowError):
                # OverflowError is the one people forget: float("inf")
                # parses fine, then int() of it blows up.
                raise InputError(f"Row {line_no} ({name}): 'arr' is not a number: {raw_arr!r}")
            if arr < 0:
                raise InputError(f"Row {line_no} ({name}): 'arr' cannot be negative.")

            health = clean.get("health", "").lower()
            if health not in VALID_HEALTH:
                raise InputError(
                    f"Row {line_no} ({name}): 'health' is {health!r}; "
                    f"expected one of {', '.join(sorted(VALID_HEALTH))}."
                )

            potential = clean.get("expansion_potential", "").lower()
            if potential not in VALID_POTENTIAL:
                raise InputError(
                    f"Row {line_no} ({name}): 'expansion_potential' is {potential!r}; "
                    f"expected one of {', '.join(sorted(VALID_POTENTIAL))}."
                )

            accounts.append(Account(name, arr, health, potential))

    if not accounts:
        raise InputError(f"{path} has a header but no data rows.")
    return accounts


def share(part: int, whole: int) -> float:
    """Percentage, safe when the denominator is zero.

    The original version of this script divided by total ARR unguarded. A
    portfolio of unpaid pilots is unusual but not impossible, and a
    ZeroDivisionError in front of a customer is a bad look.
    """
    if whole <= 0:
        return 0.0
    return round(part / whole * 100, 1)


def render(accounts: list[Account]) -> str:
    total = sum(a.arr for a in accounts)
    by_health: dict[str, list[Account]] = defaultdict(list)
    for account in accounts:
        by_health[account.health].append(account)

    lines = ["PORTFOLIO HEALTH SUMMARY", "=" * 66]
    lines.append(f"Accounts: {len(accounts)} | Total ARR: ${total:,}")
    if total == 0:
        lines.append("Note: total ARR is zero, so all shares below read 0%.")
    lines.append("")

    lines.append("ARR BY HEALTH")
    lines.append("-" * 66)
    for status in ("green", "yellow", "red"):
        bucket = by_health.get(status, [])
        arr = sum(a.arr for a in bucket)
        lines.append(
            f"  {status.title():<8} {len(bucket):>3} accounts   "
            f"${arr:>12,}   {share(arr, total):>5.1f}% of book"
        )
    lines.append("")

    at_risk = sorted(by_health.get("red", []), key=lambda a: -a.arr)
    if at_risk:
        risk_arr = sum(a.arr for a in at_risk)
        lines.append(f"RED ACCOUNTS - ${risk_arr:,} exposed ({share(risk_arr, total)}% of book)")
        lines.append("-" * 66)
        for account in at_risk:
            lines.append(f"  {account.name:<28} ${account.arr:>12,}")
        lines.append("")

    targets = sorted(
        (a for a in accounts if a.expansion_potential == "high" and a.health == "green"),
        key=lambda a: -a.arr,
    )
    lines.append(f"EXPANSION TARGETS - healthy and high potential ({len(targets)})")
    lines.append("-" * 66)
    if targets:
        for account in targets:
            lines.append(f"  {account.name:<28} ${account.arr:>12,}")
    else:
        lines.append("  None. Every high-potential account has an open health issue first.")
    lines.append("")

    concentration = max((a.arr for a in accounts), default=0)
    lines.append(
        f"Largest single account is {share(concentration, total)}% of the book."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarise portfolio ARR by health tier and flag expansion targets.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=str(DEFAULT_CSV),
        help=f"CSV of accounts (default: {DEFAULT_CSV.name} in demos/data).",
    )
    args = parser.parse_args(argv)

    try:
        accounts = parse_accounts(Path(args.source))
    except InputError as exc:
        print(f"Input problem: {exc}", file=sys.stderr)
        return 1

    print(render(accounts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
