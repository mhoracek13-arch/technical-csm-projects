"""Tests for the demo scripts.

These are labelled demos, but "demo" is not a reason to skip tests. The
most important test in this file is
`test_output_actually_depends_on_the_input` - the script it replaces
returned a fixed list no matter what you fed it, and a test is the only
thing that stops that from happening again.
"""

from __future__ import annotations

import pytest

from demos.churn_signals import assess, band_for, find_signals, score
from demos.meeting_actions import extract_action_items, find_urgency, split_sentences
from demos.portfolio_health import InputError, Account, parse_accounts, render, share


# ==========================================================================
# meeting_actions
# ==========================================================================

def test_output_actually_depends_on_the_input():
    """The regression test that matters most in this repo."""
    first = extract_action_items("Bob will send the pricing deck tomorrow.")
    second = extract_action_items("Alice needs to review the SOW today.")
    assert first != second
    assert first[0].assignee == "Bob"
    assert second[0].assignee == "Alice"


def test_hard_wrapped_sentences_are_not_chopped():
    """Pasted notes wrap mid-sentence; the deadline lives at the end."""
    wrapped = "Alice needs to audit the Q4 close\nplans today."
    items = extract_action_items(wrapped)
    assert len(items) == 1
    assert "today" in items[0].task
    assert items[0].urgency == "Urgent"


def test_blank_line_is_still_a_boundary():
    text = "Bob will send the deck\n\nAlice will review the contract"
    assert len(split_sentences(text)) == 2


def test_commentary_is_not_an_action_item():
    text = "Revenue is tracking ahead of plan. The weather is nice."
    assert extract_action_items(text) == []


def test_imperative_sentence_is_detected():
    items = extract_action_items("Review the SOW before Friday.")
    assert len(items) == 1
    assert items[0].urgency == "High"


def test_task_is_cut_at_the_commitment_marker():
    """The action, not the commentary that preceded it."""
    text = "The integration bug is blocking Helix, so Dana has to escalate it immediately."
    item = extract_action_items(text)[0]
    assert item.task == "Escalate it immediately"
    assert item.assignee == "Dana"
    assert item.urgency == "Urgent"


def test_trailing_rationale_is_trimmed():
    item = extract_action_items("Bob will fix the sync because customers complained.")[0]
    assert "because" not in item.task.lower()


def test_lowercase_department_word_is_not_read_as_an_owner():
    """Regression: 'the technical support request' is not a task for Support."""
    item = extract_action_items("We should submit the technical support request this week.")[0]
    assert item.assignee == "Team"


def test_capitalised_department_is_read_as_an_owner():
    item = extract_action_items("Finance will confirm the adjustment tomorrow.")[0]
    assert item.assignee == "Finance"


def test_first_person_maps_to_speaker():
    item = extract_action_items("I'll follow up on the paperwork.")[0]
    assert item.assignee == "Speaker"


def test_restated_commitments_are_deduplicated():
    text = "Bob will send the deck. Bob will send the deck."
    assert len(extract_action_items(text)) == 1


def test_empty_input_yields_nothing():
    assert extract_action_items("") == []
    assert extract_action_items("   \n\n  ") == []


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("this is a blocker", "Urgent"),
        ("we need this today", "Urgent"),
        ("by Friday please", "High"),
        ("before Monday", "High"),
        ("next week is fine", "Medium"),
        ("when you get a chance", "Low"),
        ("no timing mentioned", "Medium"),
    ],
)
def test_urgency_rules(phrase, expected):
    assert find_urgency(phrase) == expected


# ==========================================================================
# churn_signals
# ==========================================================================

def test_positive_and_neutral_feedback_are_different_scores():
    """Regression: the original clamped both to zero, erasing the difference."""
    happy = score(find_signals("We love it and we are expanding to two more teams."))
    silent = score(find_signals("Quarterly review went fine."))
    assert happy < 0
    assert silent == 0
    assert happy != silent


def test_negative_feedback_scores_positive_risk():
    assert score(find_signals("Frustrated with the bug and support is unresponsive.")) > 0


def test_stronger_phrases_carry_more_weight():
    cancelling = score(find_signals("We are cancelling."))
    pricey = score(find_signals("It is expensive."))
    assert cancelling > pricey


def test_score_is_clamped_to_the_readable_range():
    piled_on = "cancelling frustrated bug broken delayed expensive unresponsive competitor"
    assert score(find_signals(piled_on)) == 100
    glowing = "love excellent renewing expanding recommend champion happy efficient"
    assert score(find_signals(glowing)) == -100


@pytest.mark.parametrize(
    "value,expected",
    [(90, "CRITICAL"), (50, "CRITICAL"), (30, "AT RISK"), (5, "WATCH"),
     (0, "STABLE"), (-20, "STABLE"), (-60, "ADVOCATE")],
)
def test_bands(value, expected):
    assert band_for(value)[0] == expected


def test_missing_fields_do_not_crash_the_assessment():
    result = assess({})
    assert result.company == "(unnamed account)"
    assert result.arr == 0
    assert result.score == 0


def test_arr_at_risk_is_zero_for_healthy_accounts():
    healthy = assess({"company": "X", "arr": 100000, "feedback": "We love it."})
    assert healthy.arr_at_risk == 0


# ==========================================================================
# portfolio_health
# ==========================================================================

def write_csv(tmp_path, body: str):
    path = tmp_path / "accounts.csv"
    path.write_text(body, encoding="utf-8")
    return path


GOOD_CSV = """name,arr,health,expansion_potential
Alpha,100000,green,high
Beta,50000,red,low
"""


def test_valid_csv_parses():
    from pathlib import Path
    accounts = parse_accounts(Path(__file__).parent.parent / "demos" / "data" / "accounts.csv")
    assert len(accounts) == 8
    assert all(isinstance(a, Account) for a in accounts)


def test_currency_formatting_in_arr_is_tolerated(tmp_path):
    path = write_csv(tmp_path, "name,arr,health,expansion_potential\nAlpha,\"$1,500\",green,high\n")
    assert parse_accounts(path)[0].arr == 1500


def test_missing_column_names_the_column(tmp_path):
    path = write_csv(tmp_path, "name,arr,health\nAlpha,100,green\n")
    with pytest.raises(InputError, match="expansion_potential"):
        parse_accounts(path)


def test_bad_arr_names_the_row(tmp_path):
    path = write_csv(tmp_path, GOOD_CSV + "Gamma,not-a-number,green,high\n")
    with pytest.raises(InputError, match="Row 4"):
        parse_accounts(path)


def test_invalid_health_value_is_rejected(tmp_path):
    path = write_csv(tmp_path, "name,arr,health,expansion_potential\nAlpha,100,purple,high\n")
    with pytest.raises(InputError, match="purple"):
        parse_accounts(path)


def test_header_only_file_is_rejected(tmp_path):
    path = write_csv(tmp_path, "name,arr,health,expansion_potential\n")
    with pytest.raises(InputError, match="no data rows"):
        parse_accounts(path)


def test_missing_file_is_rejected():
    from pathlib import Path
    with pytest.raises(InputError, match="No such file"):
        parse_accounts(Path("/nope/accounts.csv"))


def test_share_guards_against_a_zero_denominator():
    """Regression: the original divided by total ARR unguarded."""
    assert share(0, 0) == 0.0
    assert share(50, 0) == 0.0
    assert share(25, 100) == 25.0


def test_zero_arr_portfolio_renders_without_crashing():
    accounts = [Account("Pilot A", 0, "green", "high"), Account("Pilot B", 0, "red", "low")]
    output = render(accounts)
    assert "total ARR is zero" in output


# ==========================================================================
# churn_signals - lexicon overlap regressions
# ==========================================================================

def test_negated_phrase_wins_over_the_shorter_positive_one():
    """Regression: "not renewing" scored as mild friction.

    "not renewing" is negative and "renewing" is positive. Scanning the two
    lexicons separately let both fire and they cancelled out, so the single
    clearest churn statement a customer can make came back as WATCH -
    "log the friction, address it in the next call".
    """
    signals = find_signals("Budget was cut, so we are not renewing next year.")
    phrases = {s.phrase for s in signals}
    assert "not renewing" in phrases
    assert "renewing" not in phrases
    assert band_for(score(signals))[0] == "CRITICAL"


def test_a_shorter_phrase_does_not_double_count_inside_a_longer_one():
    """Regression: "cancel" matched inside "cancelling", charging twice."""
    signals = find_signals("We are cancelling the contract.")
    assert len(signals) == 1
    assert signals[0].phrase == "cancelling"


def test_repeating_a_phrase_does_not_stack_the_score():
    once = score(find_signals("There is a bug."))
    thrice = score(find_signals("A bug, another bug, and a third bug."))
    assert once == thrice


def test_word_stems_are_matched():
    """"bugs" should score the same as "bug"; "escalated" the same as "escalate"."""
    assert score(find_signals("We filed three bugs.")) > 0
    assert score(find_signals("This was escalated last week.")) > 0


def test_the_bundled_feedback_fixture_loads_and_scores():
    from pathlib import Path
    from demos.churn_signals import load_records
    path = Path(__file__).parent.parent / "demos" / "data" / "feedback.json"
    records = load_records(str(path))
    assert len(records) == 6
    assessments = [assess(r) for r in records]
    assert any(a.band == "CRITICAL" for a in assessments)
    assert any(a.band == "ADVOCATE" for a in assessments)


# ==========================================================================
# meeting_actions - deadline preservation
# ==========================================================================

def test_as_soon_as_possible_is_urgent_and_kept_in_the_task():
    """Regression: splitting on "as" deleted the deadline from the task."""
    item = extract_action_items("Send the report as soon as possible.")[0]
    assert item.task == "Send the report as soon as possible"
    assert item.urgency == "Urgent"


# ==========================================================================
# portfolio_health - numeric edge case
# ==========================================================================

def test_infinite_arr_is_a_clean_input_error_not_a_traceback(tmp_path):
    """int(float("inf")) raises OverflowError, which ValueError alone misses."""
    path = write_csv(tmp_path, "name,arr,health,expansion_potential\nAlpha,inf,green,high\n")
    with pytest.raises(InputError, match="Row 2"):
        parse_accounts(path)
