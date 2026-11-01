"""Anthropic: Admin API usage/cost reports (admin key, org accounts only).

Uses the documented endpoints under /v1/organizations/*; responses are parsed
defensively (amounts summed by key name) because the report shapes have
changed before and the newer Analytics API may supersede them."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..models import AccountResult, Status
from .base import Provider


class AnthropicProvider(Provider):
    name = "anthropic"
    BASE = "https://api.anthropic.com"
    VERSION = "2023-06-01"

    def _headers(self) -> dict:
        return {
            "x-api-key": self.key,
            "anthropic-version": self.VERSION,
            "content-type": "application/json",
        }

    @staticmethod
    def _sum_by_keys(node: Any, keys: tuple[str, ...]) -> float:
        """Recursively sum numeric values found under any of the given keys."""
        total = 0.0
        if isinstance(node, dict):
            for key, value in node.items():
                if key in keys and isinstance(value, (int, float)):
                    total += float(value)
                else:
                    total += AnthropicProvider._sum_by_keys(value, keys)
        elif isinstance(node, list):
            for item in node:
                total += AnthropicProvider._sum_by_keys(item, keys)
        return total

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        today = datetime.now(timezone.utc).date()
        month_start = today.replace(day=1)
        headers = self._headers()
        params = {
            "start_date": month_start.isoformat(),
            "end_date": (today + timedelta(days=1)).isoformat(),
        }

        notes: list[str] = []
        spent_month: float | None = None
        tokens_month: float | None = None

        resp = await client.get(
            f"{self.BASE}/v1/organizations/cost_report", headers=headers, params=params
        )
        if resp.status_code != 200:
            hint = ""
            if resp.status_code in (401, 403) and "organization" in resp.text.lower():
                hint = " — the Admin API is unavailable for personal accounts (org required)"
            elif resp.status_code in (401, 403):
                hint = " — requires an *admin* key (sk-ant-admin...)"
            return self.result(
                self.classify(resp.status_code),
                error=f"HTTP {resp.status_code}: {resp.text[:200]}{hint}",
            )
        body = await self.safe_json(resp)
        if body is not None:
            spent_month = round(self._sum_by_keys(body, ("amount", "cost", "total_cost")), 4)
            if spent_month == 0.0 and not _has_any(body, ("amount", "cost", "total_cost")):
                notes.append("cost_report: unrecognized response shape")

        try:
            resp2 = await client.get(
                f"{self.BASE}/v1/organizations/usage_report/messages",
                headers=headers,
                params=params,
            )
            if resp2.status_code == 200:
                usage = await self.safe_json(resp2)
                if usage is not None:
                    tokens = round(
                        self._sum_by_keys(usage, ("input_tokens", "output_tokens")), 0
                    )
                    tokens_month = tokens
        except httpx.HTTPError:
            pass

        return self.result(
            Status.OK,
            currency="USD",
            spent_month=spent_month,
            tokens_month=tokens_month,
            last_error="; ".join(notes) or None,
        )


def _has_any(node: Any, keys: tuple[str, ...]) -> bool:
    if isinstance(node, dict):
        return any(k in node for k in keys) or any(_has_any(v, keys) for v in node.values())
    if isinstance(node, list):
        return any(_has_any(item, keys) for item in node)
    return False
