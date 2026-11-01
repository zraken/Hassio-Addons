"""OpenAI: org spend via the Usage API (admin key required).

GET /v1/organization/costs with daily buckets; no remaining-credit endpoint
exists, so we report spent_today / spent_month (UTC days)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..models import AccountResult, Status
from .base import Provider


class OpenAIProvider(Provider):
    name = "openai"
    BASE = "https://api.openai.com/v1"

    async def _costs(self, client: httpx.AsyncClient, start: datetime, limit: int) -> float | None:
        headers = {"Authorization": f"Bearer {self.key}"}
        params = {"start_time": int(start.timestamp()), "limit": limit}
        total = 0.0
        seen = 0
        for _ in range(3):  # small pagination safety
            resp = await client.get(
                f"{self.BASE}/organization/costs", headers=headers, params=params
            )
            if resp.status_code != 200:
                hint = (
                    " — this endpoint requires an *admin* key (sk-admin-...), created at "
                    "platform.openai.com/settings/organization/admin-keys"
                    if resp.status_code in (401, 403)
                    else ""
                )
                raise _HttpError(resp.status_code, f"HTTP {resp.status_code}: {resp.text[:200]}{hint}")
            body = await self.safe_json(resp) or {}
            for bucket in body.get("data") or []:
                for r in bucket.get("results") or []:
                    amount = r.get("amount") or {}
                    value = self._float(amount.get("value"))
                    if value is not None:
                        total += value
                        seen += 1
            if not body.get("has_more") or not body.get("next_page"):
                break
            params["page"] = body["next_page"]
        return total if seen else (0.0 if "data" in body else None)

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        try:
            spent_today = await self._costs(client, today_start, 1)
            spent_month = await self._costs(client, month_start, 32)
        except _HttpError as exc:
            return self.result(self.classify(exc.code), error=exc.message)

        return self.result(
            Status.OK,
            currency="USD",
            spent_today=round(spent_today, 4) if spent_today is not None else None,
            spent_month=round(spent_month, 4) if spent_month is not None else None,
        )


class _HttpError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
