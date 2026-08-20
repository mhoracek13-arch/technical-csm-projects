"""Capacity analysis: turn raw Wrike records into a workload picture.

This module is deliberately free of network calls, printing, and argparse.
Everything is a pure function over plain data, which is what makes it
testable. All the I/O lives in `sources.py` and `cli.py`.

The workload model
------------------
Wrike gives each task an optional ``effortAllocation.totalEffort`` in
minutes. For a person we compare:

    allocated minutes (sum of their active task effort)
    ----------------------------------------------------
    capacity minutes (working days in window x hours/day x 60)

Tasks with no effort estimate get a configurable default rather than being
silently treated as zero work, because "unestimated" is not the same as
"free" - and a model that quietly ignores half the book is worse than no
model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

DEFAULT_TASK_MINUTES = 240  # 4h: an unestimated task is not a free task
DEFAULT_HOURS_PER_DAY = 8
DEFAULT_WORK_DAYS = 5
DEFAULT_THRESHOLD = 80.0

# When we move work onto someone, we stop short of the threshold rather
# than filling them to the brim. Solving one overload by creating another
# is not a solution.
SAFETY_BUFFER = 5.0


@dataclass(frozen=True)
class Assignment:
    """One unit of work sitting on someone's plate."""

    task_id: str
    title: str
    minutes: int
    estimated: bool  # False means we substituted the default

    @property
    def hours(self) -> float:
        return round(self.minutes / 60, 1)


@dataclass
class Member:
    """A person plus their current load.

    `frozen=False` because the reassignment planner needs to move
    assignments around. It always works on copies, never on the caller's
    objects - see `plan_reassignments`.
    """

    user_id: str
    name: str
    capacity_minutes: int
    assignments: list[Assignment] = field(default_factory=list)

    @property
    def allocated_minutes(self) -> int:
        return sum(a.minutes for a in self.assignments)

    @property
    def load_percent(self) -> float:
        # A person with zero capacity (fully on leave, say) is not at 0%
        # load, they are unavailable. Reporting inf would be accurate but
        # unhelpful, so we surface 100% and let them be excluded as a helper.
        if self.capacity_minutes <= 0:
            return 100.0
        return round(self.allocated_minutes / self.capacity_minutes * 100, 1)

    @property
    def headroom_minutes(self) -> int:
        return max(0, self.capacity_minutes - self.allocated_minutes)

    @property
    def is_available(self) -> bool:
        """False for people with no capacity in this window - full leave, say.

        They report 100% load so nobody hands them work, but they are not
        *overloaded*, and reporting them as an unresolvable overload would
        be a false alarm. Every overload list filters on this.
        """
        return self.capacity_minutes > 0

    def copy(self) -> "Member":
        # `replace` from dataclasses gives a shallow copy; we rebuild the
        # list so mutating one Member never leaks into another.
        return replace(self, assignments=list(self.assignments))


@dataclass(frozen=True)
class Reassignment:
    """A proposed move of one task from one person to another."""

    task_id: str
    task_title: str
    minutes: int
    from_name: str
    to_name: str
    from_load_after: float
    to_load_after: float


@dataclass(frozen=True)
class CapacityReport:
    """Everything the CLI needs to render, and nothing it doesn't."""

    members: list[Member]  # post-plan state
    overloaded: list[Member]  # who was over before any moves
    reassignments: list[Reassignment]
    unresolved: list[Member]  # still over after every possible move
    threshold: float
    window_days: int

    @property
    def total_allocated_hours(self) -> float:
        return round(sum(m.allocated_minutes for m in self.members) / 60, 1)

    @property
    def average_load(self) -> float:
        if not self.members:
            return 0.0
        return round(sum(m.load_percent for m in self.members) / len(self.members), 1)


def capacity_minutes(work_days: int, hours_per_day: float) -> int:
    """Minutes of available work time in the window."""
    return int(max(0, work_days) * max(0.0, hours_per_day) * 60)


def _task_minutes(task: dict) -> tuple[int, bool]:
    """Pull effort off a Wrike task, reporting whether it was a real estimate.

    Returns ``(minutes, was_estimated)``.
    """
    effort = task.get("effortAllocation") or {}
    total = effort.get("totalEffort")
    if isinstance(total, (int, float)) and total > 0:
        return int(total), True
    return DEFAULT_TASK_MINUTES, False


def build_members(
    contacts: list[dict],
    tasks: list[dict],
    work_days: int = DEFAULT_WORK_DAYS,
    hours_per_day: float = DEFAULT_HOURS_PER_DAY,
) -> list[Member]:
    """Join contacts to tasks and compute each person's load.

    Skips deleted accounts, non-Person records (groups), and collaborators.
    None of those are part of the team's delivery capacity, and counting
    them would dilute the averages.
    """
    people: dict[str, Member] = {}
    per_person_capacity = capacity_minutes(work_days, hours_per_day)

    for contact in contacts:
        if contact.get("deleted"):
            continue
        if contact.get("type") not in (None, "Person"):
            continue  # skips Group records
        # Collaborators are type "Person"; what marks them out is the role
        # on their profile. They do not carry delivery work, so they are not
        # part of capacity.
        profiles = contact.get("profiles") or []
        if any(p.get("role") == "Collaborator" for p in profiles):
            continue
        user_id = contact.get("id")
        if not user_id:
            continue
        first = (contact.get("firstName") or "").strip()
        last = (contact.get("lastName") or "").strip()
        name = f"{first} {last}".strip() or user_id
        people[user_id] = Member(
            user_id=user_id,
            name=name,
            capacity_minutes=per_person_capacity,
        )

    for task in tasks:
        responsible = task.get("responsibleIds") or []
        if not responsible:
            continue  # unassigned work is a separate problem, not a load
        minutes, estimated = _task_minutes(task)
        # Wrike allows several assignees on one task. Splitting the effort
        # is the honest reading; giving each of them the full estimate
        # would double-count the team's workload.
        #
        # The remainder is handed to the first assignees one minute at a
        # time so the shares still sum to the original estimate. Floor
        # division alone would quietly lose minutes: 100 across 3 people
        # would book 99.
        count = len(responsible)
        base, remainder = divmod(minutes, count)
        for index, user_id in enumerate(responsible):
            member = people.get(user_id)
            if member is None:
                continue  # assignee outside our contact set
            member.assignments.append(
                Assignment(
                    task_id=task.get("id", "unknown"),
                    title=task.get("title", "(untitled task)"),
                    minutes=max(1, base + (1 if index < remainder else 0)),
                    estimated=estimated,
                )
            )

    return list(people.values())


def plan_reassignments(
    members: list[Member],
    threshold: float = DEFAULT_THRESHOLD,
    buffer: float = SAFETY_BUFFER,
    window_days: int = DEFAULT_WORK_DAYS,
) -> CapacityReport:
    """Propose task moves that bring people back under the threshold.

    Greedy algorithm, run to a fixed point:

    1. Find the most overloaded person who still has movable work.
    2. Walk their tasks largest-first and pick the first one that has a
       legal home - a teammate for whom receiving it stays under
       ``threshold - buffer``.
    3. Move it, then recompute both people's loads.
    4. Repeat. If none of a person's tasks can be placed anywhere, mark
       them exhausted and move on to the next donor.
    5. Anyone still over threshold at the end is reported as unresolved.

    Step 3 is the bit the first version of this script got wrong: it chose
    one helper up front and handed them every task without ever updating
    their workload, so a single person at 45% would absorb an unlimited
    amount of work and still report 45%.

    Step 2 matters too. Trying only the largest task and giving up gets you
    stuck: a 24h task may have no home while the 8h task next to it does.

    Greedy is not optimal - this is bin packing, which is NP-hard - but it
    is predictable and explainable, which matters more when a human has to
    defend the suggestion in a standup.
    """
    working = [m.copy() for m in members]
    original_overloaded = [
        m.copy() for m in working if m.is_available and m.load_percent > threshold
    ]
    reassignments: list[Reassignment] = []

    receiving_cap = threshold - buffer
    # Donors we have already proven cannot shed anything. Without this the
    # loop would keep re-picking the same stuck person forever.
    exhausted: set[str] = set()

    # Hard bound on iterations. Every pass either moves a task or exhausts
    # a donor, so tasks + people is a ceiling we cannot exceed.
    max_passes = sum(len(m.assignments) for m in working) + len(working)

    for _ in range(max_passes):
        donors = [
            m
            for m in working
            if m.is_available
            and m.load_percent > threshold
            and m.assignments
            and m.user_id not in exhausted
        ]
        if not donors:
            break
        donor = max(donors, key=lambda m: m.load_percent)

        def legal_helpers(minutes: int) -> list[Member]:
            return [
                m
                for m in working
                if m.user_id != donor.user_id
                and m.capacity_minutes > 0
                and (m.allocated_minutes + minutes) / m.capacity_minutes * 100 <= receiving_cap
            ]

        # Largest first: the biggest task that fits buys the most relief.
        moved = False
        for task in sorted(donor.assignments, key=lambda a: a.minutes, reverse=True):
            candidates = legal_helpers(task.minutes)
            if not candidates:
                continue
            helper = max(candidates, key=lambda m: m.headroom_minutes)

            donor.assignments.remove(task)
            helper.assignments.append(task)
            reassignments.append(
                Reassignment(
                    task_id=task.task_id,
                    task_title=task.title,
                    minutes=task.minutes,
                    from_name=donor.name,
                    to_name=helper.name,
                    from_load_after=donor.load_percent,
                    to_load_after=helper.load_percent,
                )
            )
            moved = True
            break

        if not moved:
            exhausted.add(donor.user_id)

    unresolved = [m for m in working if m.is_available and m.load_percent > threshold]

    return CapacityReport(
        members=sorted(working, key=lambda m: m.load_percent, reverse=True),
        overloaded=sorted(original_overloaded, key=lambda m: m.load_percent, reverse=True),
        reassignments=reassignments,
        unresolved=sorted(unresolved, key=lambda m: m.load_percent, reverse=True),
        threshold=threshold,
        window_days=window_days,
    )
