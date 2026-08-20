#!/usr/bin/env python3
"""Keyword-based churn signal scorer for free-text customer feedback.

This is a lexicon heuristic, not a churn model. It counts weighted
positive and negative phrases and produces a signed score. Calling that
"AI churn prediction" would be a lie, and an interviewer who reads the
code will spot the gap immediately - so the docstring says what it is.

Why keep it at all? Because a transparent baseline is genuinely useful.
It runs in milliseconds, needs no training data, and every score can be
explained by pointing at the words that produced it. If you later train a
real model, this is what you benchmark against.

Known limitations, in the order they will bite you:
  * No negation handling. "not frustrated at all" scores as negative.
  * No sarcasm, no intensity ("slightly annoyed" == "furious").
  * Phrase list is hand-built, so coverage is whatever you remembered.
  * Score magnitude is not a probability. It is a sorting key.

Usage:
    python demos/churn_signals.py                       # bundled sample
    python demos/churn_signals.py demos/data/feedback.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Weighted phrases. Weights are judgement calls, not fitted parameters -
# "cancelling" is a far stronger signal than "expensive", so it counts more.
NEGATIVE_SIGNALS: dict[str, int] = {
    "cancelling": 60,
    "cancel": 55,
    "not renewing": 60,
    "looking at alternatives": 45,
    "competitor": 30,
    "unresponsive": 25,
    "escalate": 20,
    "frustrated": 20,
    "frustrating": 20,
    "disappointed": 20,
    "blocker": 20,
    "bug": 15,
    "broken": 20,
    "delayed": 15,
    "slow": 10,
    "expensive": 15,
    "too costly": 15,
    "confusing": 10,
    "training gap": 10,
    "low adoption": 20,
}

POSITIVE_SIGNALS: dict[str, int] = {
    "renewing": 30,
    "renewed": 30,
    "expanding": 30,
    "more licences": 25,
    "more licenses": 25,
    "love": 20,
    "excellent": 20,
    "great support": 20,
    "saving": 20,
    "saved us": 20,
    "efficient": 15,
    "recommend": 20,
    "rolling out": 15,
    "champion": 15,
    "happy": 15,
    "smooth": 10,
}

# Score bands. Kept separate from the lexicon so you can retune the
# thresholds without touching the phrase weights.
BANDS: list[tuple[int, str, str]] = [
    (50, "CRITICAL", "Trigger a save play. Exec sponsor call this week."),
    (25, "AT RISK", "Named owner, written action plan, weekly check-in."),
    (1, "WATCH", "Log the friction. Address it in the next scheduled call."),
    (-24, "STABLE", "No action needed. Keep the cadence."),
    (-1000, "ADVOCATE", "Ask for the reference or the expansion conversation."),
]


@dataclass(frozen=True)
class Signal:
    phrase: str
    weight: int
    polarity: str  # "negative" or "positive"


@dataclass(frozen=True)
class Assessment:
    company: str
    arr: int
    score: int
    band: str
    recommendation: str
    signals: list[Signal]

    @property
    def arr_at_risk(self) -> int:
        """ARR weighted by score, as a crude prioritisation number.

        Not a forecast. A 200k account at WATCH deserves attention before a
        20k account at CRITICAL, and this is the smallest thing that
        expresses that ordering.
        """
        return int(self.arr * max(0, self.score) / 100)


def find_signals(text: str) -> list[Signal]:
    """Match lexicon phrases against the text, consuming each span once.

    Two subtleties, both learned the hard way:

    1. **Longest phrase first, across both lexicons.** "not renewing" is
       negative and "renewing" is positive. Scanning each lexicon
       separately let both fire, and they cancelled out - so "we are not
       renewing next year" scored as mild friction instead of a save
       situation. Merging the lists and matching longest-first means the
       longer, more specific phrase claims the span.

    2. **Matched spans are consumed.** Without that, "cancel" matches
       inside "cancelling" and the account is charged for both.

    The pattern also anchors both ends with ``\b``, so "cancel" no longer
    matches the middle of a longer word.
    """
    lowered = text.lower()
    claimed: list[tuple[int, int]] = []
    found: list[Signal] = []

    merged = [(phrase, weight, "negative") for phrase, weight in NEGATIVE_SIGNALS.items()]
    merged += [(phrase, weight, "positive") for phrase, weight in POSITIVE_SIGNALS.items()]
    # Longest first so the specific phrase wins the span it shares with a
    # shorter one; alphabetical as a tie-break keeps the output stable.
    merged.sort(key=lambda item: (-len(item[0]), item[0]))

    for phrase, weight, polarity in merged:
        for match in re.finditer(rf"\b{re.escape(phrase)}\w*", lowered):
            span = (match.start(), match.end())
            if any(start < span[1] and span[0] < end for start, end in claimed):
                continue  # this text is already accounted for
            claimed.append(span)
            found.append(Signal(phrase=phrase, weight=weight, polarity=polarity))
            break  # count each phrase once per account, not once per mention

    return found


def score(signals: list[Signal]) -> int:
    """Signed score: positive means risk, negative means health.

    The original version of this script clamped to ``max(0, ...)``, which
    made an enthusiastic customer and a silent one both score zero. Keeping
    the sign means "actively happy" and "nothing detected" stay different
    facts, which is the whole point of scoring feedback.
    """
    total = sum(s.weight if s.polarity == "negative" else -s.weight for s in signals)
    return max(-100, min(100, total))


def band_for(value: int) -> tuple[str, str]:
    for floor, label, action in BANDS:
        if value >= floor:
            return label, action
    return "UNKNOWN", "No band matched."


def assess(record: dict) -> Assessment:
    signals = find_signals(record.get("feedback", ""))
    value = score(signals)
    label, action = band_for(value)
    return Assessment(
        company=record.get("company", "(unnamed account)"),
        arr=int(record.get("arr", 0) or 0),
        score=value,
        band=label,
        recommendation=action,
        signals=signals,
    )


def render(assessments: list[Assessment]) -> str:
    lines = ["CHURN SIGNAL SCAN (keyword heuristic, not a model)", "=" * 70]
    if not assessments:
        lines.append("No records to score.")
        return "\n".join(lines)

    total_arr = sum(a.arr for a in assessments)
    flagged = [a for a in assessments if a.score > 0]
    lines.append(
        f"Accounts: {len(assessments)} | Portfolio ARR: ${total_arr:,} | "
        f"Showing friction: {len(flagged)}"
    )
    lines.append("")

    # Worst first, then by ARR, so the biggest problems lead.
    for a in sorted(assessments, key=lambda x: (-x.score, -x.arr)):
        lines.append(f"{a.company}  (ARR ${a.arr:,})")
        shown = f"{a.score:+d}" if a.score else "0"
        lines.append(f"  Band: {a.band}   Score: {shown}")
        if a.signals:
            negatives = [s.phrase for s in a.signals if s.polarity == "negative"]
            positives = [s.phrase for s in a.signals if s.polarity == "positive"]
            if negatives:
                lines.append(f"  Negative phrases: {', '.join(negatives)}")
            if positives:
                lines.append(f"  Positive phrases: {', '.join(positives)}")
        else:
            lines.append("  No lexicon phrases matched - score is not evidence of health.")
        lines.append(f"  Suggested next step: {a.recommendation}")
        if a.score > 0:
            lines.append(f"  Prioritisation figure: ${a.arr_at_risk:,}")
        lines.append("")

    lines.append(
        "Reminder: scores are a sorting key over free text, not probabilities."
    )
    return "\n".join(lines)


SAMPLE = [
    {
        "company": "Alpha Corp",
        "arr": 65000,
        "feedback": "We are frustrated with a persistent bug and support has been unresponsive. Leadership is looking at alternatives.",
    },
    {
        "company": "Beta Logistics",
        "arr": 120000,
        "feedback": "The automation workflows are saving our team hours every week. We love it and we are expanding to two more departments.",
    },
    {
        "company": "Gamma Media",
        "arr": 45000,
        "feedback": "Renewal is coming up and leadership thinks it is expensive given our delayed rollout.",
    },
    {
        "company": "Delta Tech",
        "arr": 220000,
        "feedback": "Rollout is going smoothly but adoption in the field team is slow and we have a training gap.",
    },
    {
        "company": "Helix Group",
        "arr": 30000,
        "feedback": "Quarterly review went fine. Nothing further from us at this stage.",
    },
]


def load_records(source: str | None) -> list[dict]:
    if not source:
        print("(No input given - using the bundled sample data.)\n")
        return SAMPLE

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("accounts", [])
    if not isinstance(payload, list):
        raise ValueError("Expected a JSON list of accounts, or {'accounts': [...]}.")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score free-text customer feedback for churn signals using a keyword lexicon.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="JSON file with company/arr/feedback records. Omit for the bundled sample.",
    )
    args = parser.parse_args(argv)

    try:
        records = load_records(args.source)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"Could not read input: {exc}", file=sys.stderr)
        return 1

    print(render([assess(r) for r in records]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
