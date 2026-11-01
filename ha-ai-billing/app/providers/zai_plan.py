"""z.ai GLM Coding Plan: quota level/usage via the monitor endpoints.

Endpoint discovered from Z.ai's official usage-query plugin:
GET {base}/api/monitor/usage/quota/limit  (Bearer coding-plan key)
base is https://api.z.ai (or https://open.bigmodel.cn for Zhipu CN).

Real payload shape (verified live 2026-11-01):
  data.level = "lite"
  data.limits = [
    {"type": "TIME_LIMIT",   # daily tools/MCP quota
     "usage": 100,           # the CAP in requests (misleading name)
     "currentValue": 7,      # used
     "remaining": 93,
     "percentage": 7,        # percent USED
     "nextResetTime": <epoch ms>,
     "usageDetails": [{"modelCode": "search-prime", "usage": 7}, ...]},
    {"type": "TOKENS_LIMIT", # rolling 5-hour token quota
     "unit": 3, "number": 5, "percentage": 5, "nextResetTime": <epoch ms>}
  ]

Mapped to the uniform schema: `plan` carries a compact summary,
requests_remaining/requests_limit carry the tools quota numbers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import AccountResult, Status
from .base import Provider


class ZaiPlanProvider(Provider):
    name = "zai_plan"
    DEFAULT_BASE = "https://api.z.ai"

    @property
    def base(self) -> str:
        return (self.account.base_url or self.DEFAULT_BASE).rstrip("/")

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        headers = {"Authorization": f"Bearer {self.key}"}
        resp = await client.get(
            f"{self.base}/api/monitor/usage/quota/limit", headers=headers
        )
        if resp.status_code != 200:
            return self.result(
                self.classify(resp.status_code),
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        body = await self.safe_json(resp)
        data = (body or {}).get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not data.get("limits"):
            return self.result(
                Status.OK,
                last_error="quota/limit: unrecognized response shape — please report",
            )

        level = data.get("level")
        parts: list[str] = [str(level)] if level else []
        attrs: dict = {}

        for lim in data.get("limits") or []:
            if not isinstance(lim, dict):
                continue
            pct = lim.get("percentage")
            ltype = lim.get("type")
            if ltype == "TIME_LIMIT":  # daily tools/MCP request quota
                cap = lim.get("usage")  # yes, "usage" is the cap
                remaining = lim.get("remaining")
                if isinstance(cap, (int, float)):
                    attrs["requests_limit"] = int(cap)
                if isinstance(remaining, (int, float)):
                    attrs["requests_remaining"] = int(remaining)
                if isinstance(pct, (int, float)):
                    attrs["usage_daily_pct"] = round(float(pct), 1)
                reset = _fmt_reset(lim.get("nextResetTime"))
                parts.append(f"tools {pct}% used" + (f", resets {reset}" if reset else ""))
            elif ltype == "TOKENS_LIMIT":  # rolling 5-hour token quota
                hours = lim.get("number")
                window = f"{hours}h" if isinstance(hours, (int, float)) else "tokens"
                if isinstance(pct, (int, float)):
                    attrs["usage_5h_pct"] = round(float(pct), 1)
                reset = _fmt_reset(lim.get("nextResetTime"))
                parts.append(f"{window} {pct}% used" + (f", resets {reset}" if reset else ""))
            else:
                parts.append(f"{ltype} {pct}% used")

        if not parts:
            parts.append(json.dumps(data, default=str)[:120])
        attrs["plan"] = " · ".join(parts)
        return self.result(Status.OK, **attrs)


def _fmt_reset(epoch_ms) -> str | None:
    if not isinstance(epoch_ms, (int, float)):
        return None
    try:
        dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        return dt.strftime("%H:%MZ")
    except (OverflowError, OSError, ValueError):
        return None
