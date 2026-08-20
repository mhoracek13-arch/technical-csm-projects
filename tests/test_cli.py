"""Tests for the command line layer.

`main()` returns an exit code instead of calling `sys.exit`, which is what
makes it callable from a test. Small design choice, large testability win.
"""

from __future__ import annotations

import json

import pytest

from capacity_guardian.cli import main


def test_offline_run_succeeds_and_prints_a_report(capsys):
    exit_code = main(["--offline"])
    out = capsys.readouterr().out

    assert "CAPACITY GUARDIAN" in out
    assert "Alice Novak" in out
    # The bundled fixtures deliberately include an overload the team cannot
    # absorb, so the tool should signal that it needs a human.
    assert exit_code == 1
    assert "ESCALATE" in out


def test_json_output_is_valid_json(capsys):
    main(["--offline", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["threshold"] == 80.0
    assert len(payload["members"]) == 4
    assert payload["reassignments"], "fixtures should produce at least one move"


def test_deleted_and_group_contacts_do_not_reach_the_report(capsys):
    main(["--offline", "--format", "json"])
    names = [m["name"] for m in json.loads(capsys.readouterr().out)["members"]]
    assert "Former Colleague" not in names
    assert "EMEA Success Team" not in names


def test_a_long_enough_window_leaves_nothing_to_do(capsys):
    """Same work spread over four weeks: nobody is over, so exit cleanly."""
    exit_code = main(["--offline", "--work-days", "20"])
    assert exit_code == 0
    assert "No action needed" in capsys.readouterr().out


def test_a_longer_window_lowers_everyones_load(capsys):
    main(["--offline", "--work-days", "10", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["average_load_percent"] < 50


def test_missing_fixture_directory_exits_with_a_clear_code(capsys):
    exit_code = main(["--offline", "--fixtures", "/nope/not/here"])
    assert exit_code == 4
    assert "Fixture problem" in capsys.readouterr().err


def test_live_mode_without_a_token_exits_five(monkeypatch, capsys):
    """5, not 2: argparse owns 2 for usage errors, and the two must differ."""
    monkeypatch.delenv("WRIKE_API_TOKEN", raising=False)
    exit_code = main([])
    assert exit_code == 5
    assert "Authentication problem" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad_args",
    [
        ["--offline", "--threshold", "0"],
        ["--offline", "--threshold", "-5"],
        ["--offline", "--work-days", "0"],
        ["--offline", "--hours-per-day", "0"],
    ],
)
def test_nonsense_arguments_are_rejected(bad_args):
    """argparse.error exits with code 2, which is the Unix convention."""
    with pytest.raises(SystemExit) as excinfo:
        main(bad_args)
    assert excinfo.value.code == 2
