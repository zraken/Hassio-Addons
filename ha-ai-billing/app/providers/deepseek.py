"""DeepSeek: documented balance endpoint GET /user/balance."""
from __future__ import annotations

import httpx

from ..models import AccountResult, Status
from .base import Provider


class DeepSeekProvider(Provider):
    name = "deepseek"
    BASE = "https://api.deepseek.com"

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        headers = {"Authorization": f"Bearer {self.key}"}
        resp = await client.get(f"{self.BASE}/user/balance", headers=headers)
        if resp.status_code != 200:
            return self.result(
                self.classify(resp.status_code),
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        body = await self.safe_json(resp) or {}
        infos = body.get("balance_infos") or []
        if not isinstance(infos, list) or not infos:
            return self.result(Status.ERROR, error="balance response had no balance_infos")

        # Prefer the currency the user configured, else the first entry.
        entry = next(
            (i for i in infos if i.get("currency") == self.account.currency),
            infos[0],
        )
        balance = self._float(entry.get("total_balance"))
        status = Status.OK if body.get("is_available", True) else Status.NO_CREDITS
        attrs = {
            "balance": balance,
            "currency": entry.get("currency"),
            "last_error": None if body.get("is_available", True)
            else "DeepSeek reports balance insufficient for API calls (is_available=false)",
        }
        return self.result(status, **attrs)
