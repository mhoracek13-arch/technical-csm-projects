"""Data model for an account brief.

The brief is the input a CSM actually writes: who the stakeholders are, what
the customer said they wanted, what the adoption numbers look like, what is
currently going wrong. Everything in `analysis.py` is derived from this.

Kept as plain dataclasses with a `from_dict` on each, rather than reaching
for pydantic. The validation needed here is small enough to read in one
sitting, and a portfolio repo shouldn't pull a dependency to save twenty
lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# Roles that a well-covered enterprise account should have named. Missing
# any of these is a real, actionable finding - "we have no economic buyer
# identified 70 days from renewal" is the kind of thing that loses deals.
REQUIRED_ROLES = ("economic_buyer", "champion", "technical_owner")
KNOWN_ROLES = REQUIRED_ROLES + ("end_user", "blocker", "executive_sponsor")

VALID_SENTIMENT = ("advocate", "positive", "neutral", "negative", "unknown")
VALID_TREND = ("growing", "flat", "declining", "unknown")
VALID_SEVERITY = ("high", "medium", "low")


class BriefError(ValueError):
    """The account brief was missing something or had an unusable value."""


def _require(data: dict, key: str, context: str) -> Any:
    if key not in data or data[key] in (None, ""):
        raise BriefError(f"{context}: missing required field '{key}'")
    return data[key]


def _parse_date(value: Any, context: str) -> date:
    if isinstance(value, date):
        return value
    try:
        # strptime, not date.fromisoformat: from 3.11 fromisoformat also
        # accepts "20270101" and "2027-01-01T00:00:00", so the same brief
        # would be valid on one CI leg and rejected on the other - and the
        # error message below would be a lie on the newer one.
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise BriefError(
            f"{context}: '{value}' is not a date in YYYY-MM-DD form"
        ) from exc


def _one_of(value: Any, allowed: tuple[str, ...], key: str, context: str) -> str:
    text = str(value or "unknown").strip().lower().replace(" ", "_")
    if text not in allowed:
        raise BriefError(
            f"{context}: '{key}' is {value!r}; expected one of {', '.join(allowed)}"
        )
    return text


@dataclass(frozen=True)
class Stakeholder:
    name: str
    role: str
    title: str = ""
    sentiment: str = "unknown"

    @classmethod
    def from_dict(cls, data: dict, index: int) -> "Stakeholder":
        ctx = f"stakeholder #{index}"
        return cls(
            name=str(_require(data, "name", ctx)),
            role=_one_of(_require(data, "role", ctx), KNOWN_ROLES, "role", ctx),
            title=str(data.get("title", "")),
            sentiment=_one_of(data.get("sentiment"), VALID_SENTIMENT, "sentiment", ctx),
        )


@dataclass(frozen=True)
class Objective:
    """Something the customer said they wanted, in their words.

    `metric`, `baseline` and `target` are optional on purpose - most briefs
    arrive without them, and spotting that absence is one of the more useful
    things this tool does. An objective nobody can measure cannot be
    reported on at a renewal.
    """

    statement: str
    metric: str = ""
    baseline: str = ""
    target: str = ""
    owner: str = ""

    @property
    def is_measurable(self) -> bool:
        return bool(self.metric.strip())

    @property
    def is_baselined(self) -> bool:
        return bool(self.metric.strip() and self.baseline.strip())

    @classmethod
    def from_dict(cls, data: dict, index: int) -> "Objective":
        ctx = f"objective #{index}"
        return cls(
            statement=str(_require(data, "statement", ctx)),
            metric=str(data.get("metric", "") or ""),
            baseline=str(data.get("baseline", "") or ""),
            target=str(data.get("target", "") or ""),
            owner=str(data.get("owner", "") or ""),
        )


@dataclass(frozen=True)
class Risk:
    description: str
    severity: str = "medium"
    mitigation: str = ""
    owner: str = ""

    @classmethod
    def from_dict(cls, data: dict, index: int) -> "Risk":
        ctx = f"risk #{index}"
        return cls(
            description=str(_require(data, "description", ctx)),
            severity=_one_of(data.get("severity", "medium"), VALID_SEVERITY, "severity", ctx),
            mitigation=str(data.get("mitigation", "") or ""),
            owner=str(data.get("owner", "") or ""),
        )


@dataclass(frozen=True)
class Adoption:
    """Seat and usage numbers. All optional - many briefs have none."""

    licences_purchased: int = 0
    licences_active: int = 0
    weekly_active_users: int = 0
    trend: str = "unknown"

    @property
    def utilisation(self) -> float | None:
        """Active over purchased, as a percentage. None when unknown.

        Returning None rather than 0.0 matters: "we have no seat data" and
        "nobody is using it" are different findings, and a report that
        conflates them will send a CSM into the wrong conversation.
        """
        if self.licences_purchased <= 0:
            return None
        return round(self.licences_active / self.licences_purchased * 100, 1)

    @property
    def idle_licences(self) -> int:
        return max(0, self.licences_purchased - self.licences_active)

    @classmethod
    def from_dict(cls, data: dict) -> "Adoption":
        ctx = "adoption"
        def as_int(key: str) -> int:
            raw = data.get(key, 0) or 0
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise BriefError(
                    f"{ctx}: '{key}' is {raw!r}; expected a whole number"
                ) from exc
            if value < 0:
                raise BriefError(f"{ctx}: '{key}' cannot be negative")
            return value

        purchased = as_int("licences_purchased")
        active = as_int("licences_active")
        if active > purchased:
            # Covers the 0-purchased case too: 50 active of 0 purchased is a
            # data-entry error, not a licence-free deployment.
            raise BriefError(
                f"{ctx}: licences_active ({active}) exceeds "
                f"licences_purchased ({purchased})"
            )
        return cls(
            licences_purchased=purchased,
            licences_active=active,
            weekly_active_users=as_int("weekly_active_users"),
            trend=_one_of(data.get("trend"), VALID_TREND, "trend", ctx),
        )


@dataclass(frozen=True)
class Account:
    name: str
    arr: int
    renewal_date: date
    csm: str = ""
    segment: str = ""
    adoption: Adoption = field(default_factory=Adoption)
    stakeholders: list[Stakeholder] = field(default_factory=list)
    objectives: list[Objective] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)
    open_escalations: int = 0
    sponsor_changed_recently: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        ctx = "account"
        if not isinstance(data, dict):
            raise BriefError("The brief must be a JSON object at the top level")

        raw_arr = _require(data, "arr", ctx)
        try:
            arr = int(float(str(raw_arr).replace(",", "").replace("$", "")))
        except (TypeError, ValueError, OverflowError) as exc:
            raise BriefError(f"{ctx}: 'arr' is {raw_arr!r}; expected a number") from exc
        if arr < 0:
            raise BriefError(f"{ctx}: 'arr' cannot be negative")

        raw_escalations = data.get("open_escalations", 0) or 0
        try:
            escalations = int(raw_escalations)
        except (TypeError, ValueError) as exc:
            raise BriefError(
                f"{ctx}: 'open_escalations' is {raw_escalations!r}; "
                "expected a whole number"
            ) from exc
        if escalations != float(raw_escalations):
            raise BriefError(
                f"{ctx}: 'open_escalations' is {raw_escalations!r}; "
                "expected a whole number"
            )
        if escalations < 0:
            # Every other numeric field raises rather than clamping. Silently
            # turning -7 into 0 would hide a typo in the brief.
            raise BriefError(f"{ctx}: 'open_escalations' cannot be negative")

        return cls(
            name=str(_require(data, "name", ctx)),
            arr=arr,
            renewal_date=_parse_date(_require(data, "renewal_date", ctx), ctx),
            csm=str(data.get("csm", "") or ""),
            segment=str(data.get("segment", "") or ""),
            adoption=Adoption.from_dict(data.get("adoption") or {}),
            stakeholders=[
                Stakeholder.from_dict(s, i)
                for i, s in enumerate(data.get("stakeholders") or [], 1)
            ],
            objectives=[
                Objective.from_dict(o, i)
                for i, o in enumerate(data.get("objectives") or [], 1)
            ],
            risks=[
                Risk.from_dict(r, i) for i, r in enumerate(data.get("risks") or [], 1)
            ],
            open_escalations=escalations,
            sponsor_changed_recently=bool(data.get("sponsor_changed_recently", False)),
            notes=str(data.get("notes", "") or ""),
        )
