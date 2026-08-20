"""Tests for the capacity analysis.

Two things worth noticing about these tests:

1. They never touch the network. All the I/O is behind `sources.py`, so the
   logic can be tested with plain dicts.
2. `test_helper_load_updates_after_each_move` is a regression test for a
   real bug in the first version of this script. That is the most valuable
   kind of test to write - it encodes a mistake so it cannot come back.
"""

from __future__ import annotations

import pytest

from capacity_guardian.capacity import (
    DEFAULT_TASK_MINUTES,
    SAFETY_BUFFER,
    Assignment,
    Member,
    build_members,
    capacity_minutes,
    plan_reassignments,
)

WEEK = capacity_minutes(work_days=5, hours_per_day=8)  # 2400 minutes


def member(name: str, *task_minutes: int, capacity: int = WEEK) -> Member:
    """Small helper so the tests read as intent, not as setup."""
    return Member(
        user_id=name.lower(),
        name=name,
        capacity_minutes=capacity,
        assignments=[
            Assignment(task_id=f"{name}-{i}", title=f"Task {i}", minutes=m, estimated=True)
            for i, m in enumerate(task_minutes)
        ],
    )


# --------------------------------------------------------------------------
# Load arithmetic
# --------------------------------------------------------------------------

def test_load_percent_is_allocated_over_capacity():
    assert member("Ann", 1200).load_percent == 50.0
    assert member("Ben", 2400).load_percent == 100.0
    assert member("Cal", 3600).load_percent == 150.0


def test_member_with_no_tasks_is_at_zero():
    assert member("Dee").load_percent == 0.0
    assert member("Dee").headroom_minutes == WEEK


def test_zero_capacity_reports_unavailable_not_free():
    """Someone on full leave has no capacity; 0% would invite giving them work."""
    unavailable = member("Eli", capacity=0)
    assert unavailable.load_percent == 100.0
    assert unavailable.headroom_minutes == 0


def test_headroom_never_goes_negative():
    assert member("Fay", 5000).headroom_minutes == 0


def test_empty_team_does_not_divide_by_zero():
    report = plan_reassignments([])
    assert report.average_load == 0.0
    assert report.total_allocated_hours == 0.0
    assert report.reassignments == []


# --------------------------------------------------------------------------
# Reassignment planning
# --------------------------------------------------------------------------

def test_no_moves_when_everyone_is_under_threshold():
    team = [member("Ann", 1200), member("Ben", 600)]
    report = plan_reassignments(team, threshold=80)
    assert report.reassignments == []
    assert report.overloaded == []
    assert report.unresolved == []


def test_overloaded_member_sheds_work_to_the_idle_one():
    team = [member("Ann", 1200, 1200), member("Ben")]
    report = plan_reassignments(team, threshold=80)

    assert len(report.reassignments) == 1
    move = report.reassignments[0]
    assert move.from_name == "Ann"
    assert move.to_name == "Ben"
    assert report.unresolved == []


def test_helper_load_updates_after_each_move():
    """Regression test for the original bug.

    Three overloaded people and one helper. The old implementation picked
    `available[0]` once and never recomputed their load, so Ben would
    receive all three tasks and still report his starting percentage.

    Ben's capacity is one week. Receiving cap is 80 - 5 = 75%, so he can
    legally absorb at most 1800 minutes. Each donor task is 1500 minutes,
    therefore exactly one task can land on him and no more.
    """
    donors = [member(f"Donor{i}", 1500, 1500) for i in range(3)]
    helper = member("Ben")
    report = plan_reassignments([*donors, helper], threshold=80)

    ben = next(m for m in report.members if m.name == "Ben")
    assert len(ben.assignments) == 1, "helper absorbed more work than they had room for"
    assert ben.load_percent <= 75.0
    assert len(report.reassignments) == 1
    # Donor0 shed a task and is now fine. Donor1 and Donor2 found no home
    # for anything, and must be reported rather than quietly dropped.
    assert {m.name for m in report.unresolved} == {"Donor1", "Donor2"}


def test_a_move_never_creates_a_new_overload():
    """A move must actually happen, or this asserts nothing.

    Earlier version of this test used Ann(2400)/Ben(1700), where Ann's only
    task was too big for Ben to ever receive. No move was attempted, so the
    assertion was checking the *input* and stayed green even with the
    capacity guard removed. Mutation testing is how you find that.

    Here Ann is at 112.5% (2000 + 700) and Ben at 41.7% (1000). Ben's
    receiving cap is 75% = 1800 minutes, so he has 800 to spare: the
    700-minute task fits, the 2000-minute one does not. A planner that
    ignored the cap would grab the biggest task and shove Ben to 125%.
    """
    team = [member("Ann", 2000, 700), member("Ben", 1000)]
    report = plan_reassignments(team, threshold=80)

    assert report.reassignments, "no move happened, so this test proves nothing"
    receiving_cap = 80 - SAFETY_BUFFER
    for move in report.reassignments:
        assert move.to_load_after <= receiving_cap
    assert report.reassignments[0].minutes == 700


def test_falls_back_to_a_smaller_task_when_the_biggest_will_not_fit():
    """The biggest task has no home, but a smaller one does.

    Ann is at 2640 (110%). Her 2400-minute task cannot go anywhere. Her
    240-minute task can. A planner that only ever tries the largest task
    would give up here and report Ann as unresolvable.
    """
    team = [member("Ann", 2400, 240), member("Ben", 1200)]
    report = plan_reassignments(team, threshold=80)

    assert len(report.reassignments) == 1
    assert report.reassignments[0].minutes == 240


def test_unresolved_when_the_whole_team_is_full():
    team = [member("Ann", 3000), member("Ben", 1800), member("Cal", 1800)]
    report = plan_reassignments(team, threshold=80)
    assert report.reassignments == []
    assert [m.name for m in report.unresolved] == ["Ann"]


def test_planner_does_not_mutate_the_caller_objects():
    """The report is a projection, not a side effect on your inputs."""
    ann = member("Ann", 1200, 1200)
    ben = member("Ben")
    before = len(ann.assignments)

    plan_reassignments([ann, ben], threshold=80)

    assert len(ann.assignments) == before
    assert len(ben.assignments) == 0


def test_zero_capacity_member_is_never_given_work():
    team = [member("Ann", 2400, 2400), member("OnLeave", capacity=0)]
    report = plan_reassignments(team, threshold=80)
    on_leave = next(m for m in report.members if m.name == "OnLeave")
    assert on_leave.assignments == []


@pytest.mark.parametrize("threshold", [50, 80, 95])
def test_recipients_stay_under_the_receiving_cap_at_any_threshold(threshold):
    """The property that actually matters, checked across thresholds.

    Deliberately not written as "the set of people over threshold equals
    report.unresolved" - that is how `capacity.py` computes `unresolved`, so
    such a test is a tautology that cannot fail for any planner behaviour.
    """
    team = [member("Ann", 2300), member("Ben", 900), member("Cal", 300)]
    report = plan_reassignments(team, threshold=threshold)

    receiving_cap = threshold - SAFETY_BUFFER
    recipients = {move.to_name for move in report.reassignments}
    for m in report.members:
        if m.name in recipients:
            assert m.load_percent <= receiving_cap


# --------------------------------------------------------------------------
# Building members from raw Wrike payloads
# --------------------------------------------------------------------------

def test_deleted_contacts_and_groups_are_skipped():
    contacts = [
        {"id": "u1", "firstName": "Ann", "lastName": "N", "type": "Person"},
        {"id": "u2", "firstName": "Gone", "lastName": "X", "type": "Person", "deleted": True},
        {"id": "g1", "title": "Some Team", "type": "Group"},
    ]
    members = build_members(contacts, tasks=[])
    assert [m.name for m in members] == ["Ann N"]


def test_unassigned_tasks_are_not_counted_as_load():
    contacts = [{"id": "u1", "firstName": "Ann", "lastName": "N", "type": "Person"}]
    tasks = [{"id": "t1", "title": "Orphan", "responsibleIds": [],
              "effortAllocation": {"totalEffort": 600}}]
    members = build_members(contacts, tasks)
    assert members[0].allocated_minutes == 0


def test_tasks_for_unknown_assignees_are_ignored():
    contacts = [{"id": "u1", "firstName": "Ann", "lastName": "N", "type": "Person"}]
    tasks = [{"id": "t1", "title": "Someone else's", "responsibleIds": ["u99"],
              "effortAllocation": {"totalEffort": 600}}]
    members = build_members(contacts, tasks)
    assert members[0].allocated_minutes == 0


def test_effort_is_split_across_multiple_assignees():
    """Giving each assignee the full estimate would double-count the team."""
    contacts = [
        {"id": "u1", "firstName": "Ann", "lastName": "N", "type": "Person"},
        {"id": "u2", "firstName": "Ben", "lastName": "L", "type": "Person"},
    ]
    tasks = [{"id": "t1", "title": "Shared", "responsibleIds": ["u1", "u2"],
              "effortAllocation": {"totalEffort": 600}}]
    members = build_members(contacts, tasks)
    assert all(m.allocated_minutes == 300 for m in members)


def test_unestimated_task_gets_the_default_and_is_flagged():
    contacts = [{"id": "u1", "firstName": "Ann", "lastName": "N", "type": "Person"}]
    tasks = [{"id": "t1", "title": "No estimate", "responsibleIds": ["u1"]}]
    members = build_members(contacts, tasks)
    assignment = members[0].assignments[0]
    assert assignment.minutes == DEFAULT_TASK_MINUTES
    assert assignment.estimated is False


def test_contact_with_no_name_falls_back_to_id():
    contacts = [{"id": "u1", "type": "Person"}]
    members = build_members(contacts, tasks=[])
    assert members[0].name == "u1"


def test_capacity_scales_with_the_window():
    contacts = [{"id": "u1", "firstName": "Ann", "lastName": "N", "type": "Person"}]
    members = build_members(contacts, tasks=[], work_days=10, hours_per_day=6)
    assert members[0].capacity_minutes == 10 * 6 * 60


def test_person_on_full_leave_is_not_reported_as_an_overload():
    """Regression: zero capacity reads as 100% load, which is not an overload.

    Before this guard, a teammate on full leave with no tasks appeared under
    OVERLOADED, triggered the "team has no headroom" escalation while a
    colleague sat at 50%, and made the CLI exit non-zero in cron.
    """
    team = [member("Ann", 1200), member("OnLeave", capacity=0)]
    report = plan_reassignments(team, threshold=80)

    assert report.overloaded == []
    assert report.unresolved == []
    on_leave = next(m for m in report.members if m.name == "OnLeave")
    assert on_leave.load_percent == 100.0  # still unavailable as a helper
    assert on_leave.is_available is False


def test_collaborators_are_not_counted_as_capacity():
    contacts = [
        {"id": "u1", "firstName": "Ann", "lastName": "N", "type": "Person",
         "profiles": [{"role": "User"}]},
        {"id": "u2", "firstName": "Ext", "lastName": "Partner", "type": "Person",
         "profiles": [{"role": "Collaborator"}]},
    ]
    assert [m.name for m in build_members(contacts, tasks=[])] == ["Ann N"]


def test_split_effort_sums_back_to_the_original_estimate():
    """Floor division alone would book 99 of a 100-minute task across 3 people."""
    contacts = [
        {"id": f"u{i}", "firstName": f"P{i}", "lastName": "X", "type": "Person"}
        for i in range(3)
    ]
    tasks = [{"id": "t1", "title": "Shared", "responsibleIds": ["u0", "u1", "u2"],
              "effortAllocation": {"totalEffort": 100}}]
    members = build_members(contacts, tasks)
    assert sum(m.allocated_minutes for m in members) == 100
