"""OpenRouter: management-key balance via /api/v1/credits."""
from __future__ import annotations

import httpx

from ..models import AccountResult, Status
from .base import Provider


class OpenRouterProvider(Provider):
    name = "openrouter"
    BASE = "https://openrouter.ai/api/v1"

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        headers = {"Authorization": f"Bearer {self.key}"}
        resp = await client.get(f"{self.BASE}/credits", headers=headers)
        if resp.status_code != 200:
            hint = ""
            if resp.status_code == 403:
                hint = " — the /credits endpoint requires a *management* key " \
                       "(create one at openrouter.ai/settings/management-keys; read-only is sufficient)"
            body = (await self.safe_json(resp)) or {}
            detail = body.get("error", {}).get("message", resp.text[:200]) if isinstance(body, dict) else resp.text[:200]
            return self.result(
                self.classify(resp.status_code),
                error=f"HTTP {resp.status_code}: {str(detail)[:200]}{hint}",
            )

        data = (await self.safe_json(resp) or {}).get("data") or {}
        total_credits = self._float(data.get("total_credits"))
        total_usage = self._float(data.get("total_usage"))
        balance = (
            round(total_credits - total_usage, 4)
            if total_credits is not None and total_usage is not None
            else None
        )

        attrs: dict = {"balance": balance, "currency": "USD"}

        # Best-effort: per-key info (label/tier/limits). Failure is non-fatal.
        try:
            resp2 = await client.get(f"{self.BASE}/auth/key", headers=headers)
            if resp2.status_code == 200:
                key_data = (await self.safe_json(resp2) or {}).get("data") or {}
                label = key_data.get("label")
                tier = "free tier" if key_data.get("is_free_tier") else "paid tier"
                attrs["plan"] = f"{label} ({tier})" if label else tier
                limit = self._float(key_data.get("limit"))
                if limit is not None:
                    attrs["requests_limit"] = limit  # per-key credit limit, USD
        except httpx.HTTPError:
            pass

        return self.result(Status.OK, **attrs)
