#!/usr/bin/env python3
"""Rule-based action item extractor for meeting notes.

This is a real parser, not a canned response. It reads whatever text you
give it and applies explicit rules. Feed it a different transcript and you
get different output - which sounds like a low bar, but the version of this
script it replaces returned a hardcoded list regardless of its input.

How it works
------------
1. Split the text into sentences.
2. Keep sentences that look like commitments (modal verbs like "needs to",
   "will", "should", or a leading imperative verb).
3. Pull an assignee out of the sentence - a known name, "I", or "we".
4. Infer urgency from time words ("today", "by Friday", "urgent").
5. Strip the commitment phrasing to leave something task-shaped.

What it is not
--------------
Rules, not an LLM and not machine learning. It will miss indirect phrasing
("can someone look at the invoice thing?") and it has no notion of context
across sentences. The point is a transparent baseline you can read, test,
and argue with - a good thing to have before reaching for a model.

Usage:
    python demos/meeting_actions.py                    # bundled sample
    python demos/meeting_actions.py notes.txt          # your own file
    echo "Ben will send the deck" | python demos/meeting_actions.py -
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Names the parser can recognise as assignees. In a real deployment this
# would come from your directory or CRM, not a literal.
KNOWN_NAMES = [
    "Alice", "Bob", "Charlie", "Dana", "Milan",
    "Finance", "Legal", "Support", "Marketing",
]

# Phrases that mark a sentence as a commitment rather than commentary.
COMMITMENT_PATTERNS = [
    r"\bneeds? to\b",
    r"\bhas to\b",
    r"\bhave to\b",
    r"\bwill\b",
    r"\bshould\b",
    r"\bmust\b",
    r"\bis going to\b",
    r"\bare going to\b",
    r"\blet's\b",
    r"\bwe need\b",
    r"\bi'll\b",
    r"\bplease\b",
    r"\baction(?: item)?:",
    r"\btodo:",
]

# Verbs that start an imperative sentence: "Send the deck by Friday."
IMPERATIVE_VERBS = {
    "send", "review", "update", "check", "confirm", "verify", "draft",
    "schedule", "book", "audit", "prepare", "share", "follow", "escalate",
    "submit", "chase", "fix", "add", "remove", "email", "call", "sync",
}

# Urgency cues, most urgent first. First match wins.
URGENCY_RULES: list[tuple[str, str]] = [
    (r"\b(urgent|asap|immediately|critical|blocker|blocking)\b", "Urgent"),
    (r"\bas soon as possible\b", "Urgent"),
    (r"\b(today|by end of day|eod|tonight|right away)\b", "Urgent"),
    (r"\b(tomorrow|this week|by end of week|eow)\b", "High"),
    (r"\b(?:by|before)\s+(?:next\s+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day\b", "High"),
    (r"\b(next week|next sprint|by month end)\b", "Medium"),
    (r"\b(eventually|at some point|when you get a chance|nice to have)\b", "Low"),
]
DEFAULT_URGENCY = "Medium"

# Leading filler to shave off the front of an extracted task.
LEADING_NOISE = re.compile(
    r"^(?:so|ok|okay|right|also|and|then|hey team|hey|team|well|just)\b[,\s]*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ActionItem:
    task: str
    assignee: str
    urgency: str
    source_sentence: str


def split_sentences(text: str) -> list[str]:
    """Split on sentence enders and paragraph breaks.

    Note what this does *not* split on: a single newline. Pasted notes are
    usually hard-wrapped mid-sentence, so treating every line break as a
    boundary chops sentences in half and loses the tail - which is where
    the deadline usually lives.

    Naive by design. A real implementation would use spaCy or NLTK; this is
    a regex you can read in one sitting, and it is honest about that.
    """
    # A blank line is a real boundary, so park it on a sentinel character
    # before collapsing the hard wraps that are not boundaries.
    sentinel = "\x00"
    normalised = re.sub(r"\n\s*\n+", sentinel, text.strip())
    normalised = re.sub(r"\s*\n\s*", " ", normalised)
    parts = re.split(rf"(?<=[.!?])\s+|{sentinel}", normalised)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


def is_commitment(sentence: str) -> bool:
    lowered = sentence.lower()
    if any(re.search(pattern, lowered) for pattern in COMMITMENT_PATTERNS):
        return True
    # Imperative check: first real word is a command verb.
    first_word = re.sub(r"[^a-z']", "", lowered.split()[0]) if lowered.split() else ""
    return first_word in IMPERATIVE_VERBS


def find_assignee(sentence: str) -> str:
    """Return a best-guess owner, or 'Unassigned'.

    Deliberately case-sensitive. Matching case-insensitively meant
    "the technical support request" was read as an assignment to the
    Support team - a good reminder that a looser rule is not a better one.
    """
    for name in KNOWN_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", sentence):
            return name
    if re.search(r"\bi'?ll\b|\bi will\b|\bi need to\b", sentence, re.IGNORECASE):
        return "Speaker"
    if re.search(r"\bwe\b|\bour\b|\bteam\b|\blet's\b", sentence, re.IGNORECASE):
        return "Team"
    return "Unassigned"


def find_urgency(sentence: str) -> str:
    lowered = sentence.lower()
    for pattern, level in URGENCY_RULES:
        if re.search(pattern, lowered):
            return level
    return DEFAULT_URGENCY


def to_task_text(sentence: str, assignee: str) -> str:
    """Turn a spoken sentence into something that reads like a task.

    The key move is cutting at the commitment marker rather than only
    trimming the front. "The integration bug is blocking Helix, so Dana has
    to escalate it today" becomes "Escalate it today" - the marker tells us
    where the commentary stops and the action starts.
    """
    text = sentence.strip().rstrip(".!?")
    text = LEADING_NOISE.sub("", text)

    # Find the earliest commitment marker and keep everything after it.
    cut_at = None
    for pattern in COMMITMENT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match and (cut_at is None or match.end() < cut_at):
            cut_at = match.end()
    if cut_at is not None:
        text = text[cut_at:]

    # Drop a leading pronoun or owner name left behind by the cut.
    text = re.sub(
        r"^\s*(?:to|that|we|i|you|they|he|she)\b\s*", "", text, flags=re.IGNORECASE
    )
    if assignee not in ("Unassigned", "Team", "Speaker"):
        text = re.sub(rf"^\s*{re.escape(assignee)}\b[,\s]*", "", text, flags=re.IGNORECASE)
    text = LEADING_NOISE.sub("", text.strip())
    # Cut trailing rationale: the task is the action, not the argument for it.
    # Note "as" is deliberately absent: splitting on it truncated
    # "send the report as soon as possible" down to "send the report",
    # deleting the deadline from the task.
    text = re.split(r"\s+(?:because|since)\s+", text, maxsplit=1)[0]
    text = text.strip(" ,;:-")

    return text[:1].upper() + text[1:] if text else sentence.strip().rstrip(".!?")


def extract_action_items(text: str) -> list[ActionItem]:
    """The whole parser. Pure function: same input, same output, no printing."""
    items: list[ActionItem] = []
    seen: set[str] = set()

    for sentence in split_sentences(text):
        if not is_commitment(sentence):
            continue
        assignee = find_assignee(sentence)
        task = to_task_text(sentence, assignee)
        if len(task.split()) < 2:
            continue  # too short to be a real action
        key = task.lower()
        if key in seen:
            continue  # the same commitment restated
        seen.add(key)
        items.append(
            ActionItem(
                task=task,
                assignee=assignee,
                urgency=find_urgency(sentence),
                source_sentence=sentence,
            )
        )
    return items


def render(items: list[ActionItem], word_count: int) -> str:
    order = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3}
    items = sorted(items, key=lambda i: order.get(i.urgency, 9))

    lines = ["ACTION ITEMS EXTRACTED", "=" * 64,
             f"Source: {word_count} words", ""]
    if not items:
        lines.append("No commitments detected. Either the notes are purely")
        lines.append("informational, or the phrasing is outside the rule set.")
        return "\n".join(lines)

    for idx, item in enumerate(items, 1):
        lines.append(f"{idx}. [{item.urgency.upper()}] {item.task}")
        lines.append(f"   Owner: {item.assignee}")
        lines.append(f'   From:  "{item.source_sentence}"')
        lines.append("")

    unassigned = sum(1 for i in items if i.assignee == "Unassigned")
    lines.append(f"{len(items)} item(s) found; {unassigned} without a clear owner.")
    return "\n".join(lines)


SAMPLE = """Hey team, thanks for joining. Revenue is tracking slightly ahead of
plan this quarter, which is good news. Alice needs to audit all the Q4 close
plans today because the numbers do not reconcile. Bob will verify the 150k
forecast adjustment with Finance before Friday. We should also submit the
technical support request through the new form this week. Charlie, please
prepare the QBR deck for Apex Retail next week. The integration bug is
blocking Helix Group, so Dana has to escalate it immediately. I'll follow up
on the renewal paperwork eventually. That's everything, thanks all."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract action items from meeting notes using explicit rules.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Path to a text file, or '-' to read stdin. Omit for the bundled sample.",
    )
    args = parser.parse_args(argv)

    if args.source == "-":
        text = sys.stdin.read()
    elif args.source:
        path = Path(args.source)
        if not path.is_file():
            print(f"No such file: {path}", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8")
    else:
        print("(No input given - using the bundled sample transcript.)\n")
        text = SAMPLE

    if not text.strip():
        print("Input was empty.", file=sys.stderr)
        return 1

    print(render(extract_action_items(text), word_count=len(text.split())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
