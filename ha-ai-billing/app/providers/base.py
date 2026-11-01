"""Provider base class: shared auth/attributes/status classification."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..config import AccountConfig
from ..models import ATTRIBUTE_KEYS, AccountResult, Status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Provider:
    """One provider instance per account. Subclasses implement fetch()."""

    name = "base"

    def __init__(self, account: AccountConfig) -> None:
        self.account = account
        self.key: str = account.api_key or ""

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def classify(code: int) -> Status:
        if code in (401, 403):
            return Status.AUTH_ERROR
        if code == 402:
            return Status.NO_CREDITS
        if code == 429:
            return Status.RATE_LIMITED
        return Status.ERROR

    def result(self, status: Status, **attrs) -> AccountResult:
        """Build an AccountResult with the uniform attribute schema.

        Unknown keys and None values are dropped; provider/key_hint/last_checked
        are always added; the convenience kwarg `error=` becomes `last_error`."""
        error = attrs.pop("error", None)
        merged: dict = {
            "provider": self.name,
            "key_hint": (self.key[-4:] if self.key else None),
            "last_checked": now_iso(),
        }
        for key, value in attrs.items():
            if value is not None and key in ATTRIBUTE_KEYS:
                merged[key] = value
        if error is not None:
            merged["last_error"] = str(error)[:250]
        return AccountResult(status=status, attributes=merged)

    @staticmethod
    async def safe_json(resp: httpx.Response):
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def _float(value) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # -- contract ------------------------------------------------------------

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        raise NotImplementedError
