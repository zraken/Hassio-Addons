"""z.ai / Zhipu pay-as-you-go balance (EXPERIMENTAL).

The documented balance endpoint lives on the Zhipu open platform
(GET {base}/api/paas/v4/users/balance). The exact z.ai international shape
is not fully verified; parsing is deliberately shape-tolerant. If the
response is not understood, the sensor stays `ok` (the key works) and the
raw shape is noted in last_error so it can be reported upstream."""
from __future__ import annotations

from typing import Any

import httpx

from ..models import AccountResult, Status
from .base import Provider


class ZaiProvider(Provider):
    name = "zai"
    DEFAULT_BASE = "https://api.z.ai"

    @property
    def base(self) -> str:
        return (self.account.base_url or self.DEFAULT_BASE).rstrip("/")

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        headers = {"Authorization": f"Bearer {self.key}"}
        resp = await client.get(f"{self.base}/api/paas/v4/users/balance", headers=headers)
        if resp.status_code != 200:
            return self.result(
                self.classify(resp.status_code),
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        body = await self.safe_json(resp)
        found = _find_balance(body if isinstance(body, dict) else {})

        if found is None:
            return self.result(
                Status.OK,
                last_error="balance response not recognized (experimental provider) — please report",
            )
        balance, currency = found
        return self.result(Status.OK, balance=balance, currency=currency)


def _find_balance(node: Any) -> tuple[float, str | None] | None:
    """Find the first plausible (amount, currency) pair under keys mentioning balance."""
    if isinstance(node, dict):
        for key, value in node.items():
            if "balance" in key.lower() and isinstance(value, (int, float)):
                currency = None
                for sibling in ("currency", "currencyCode"):
                    if isinstance(node.get(sibling), str):
                        currency = node[sibling]
                        break
                return round(float(value), 4), currency
        for value in node.values():
            found = _find_balance(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_balance(item)
            if found is not None:
                return found
    return None
