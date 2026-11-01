"""Shared data model: sensor states and the uniform attribute schema."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    """State of the one-sensor-per-account 'key health' entity."""

    OK = "ok"
    AUTH_ERROR = "auth_error"
    NO_CREDITS = "no_credits"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"
    UNKNOWN = "unknown"


#: The uniform attribute schema. Every account's sensor exposes (a subset of)
#: these keys; providers only populate what they can actually report.
ATTRIBUTE_KEYS: tuple[str, ...] = (
    "provider",
    "key_hint",
    "plan",
    "balance",
    "currency",
    "spent_today",
    "spent_month",
    "tokens_month",
    "usage_daily_pct",
    "usage_5h_pct",
    "requests_remaining",
    "requests_limit",
    "tokens_remaining",
    "tokens_limit",
    "last_error",
    "last_checked",
)

#: Attribute fields that may be promoted to real numeric entities via `track:`.
TRACKABLE_FIELDS: frozenset[str] = frozenset(
    {
        "balance",
        "spent_today",
        "spent_month",
        "tokens_month",
        "usage_daily_pct",
        "usage_5h_pct",
        "requests_remaining",
        "tokens_remaining",
    }
)


@dataclass
class AccountResult:
    """Outcome of one provider poll for one account."""

    status: Status = Status.UNKNOWN
    attributes: dict = field(default_factory=dict)
