"""Meta Model API (api.meta.ai, OpenAI-compatible): key health + rate-limit window.

No usage/balance endpoint exists, but every token-bearing response carries
x-ratelimit-* headers, so a 1-token probe yields the live quota window.
Limits are per *team*, not per key."""
from __future__ import annotations

import httpx

from ..models import AccountResult, Status
from .base import Provider


class MetaProvider(Provider):
    name = "meta"
    DEFAULT_BASE = "https://api.meta.ai/v1"
    DEFAULT_PROBE_MODEL = "muse-spark-1.3"

    @property
    def base(self) -> str:
        return (self.account.base_url or self.DEFAULT_BASE).rstrip("/")

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        headers = {"Authorization": f"Bearer {self.key}"}

        resp = await client.get(f"{self.base}/models", headers=headers)
        if resp.status_code != 200:
            return self.result(
                self.classify(resp.status_code),
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        probe = self.account.probe if self.account.probe is not None else True
        if not probe:
            return self.result(Status.OK)

        model = self.account.probe_model or self.DEFAULT_PROBE_MODEL
        try:
            probe_resp = await client.post(
                f"{self.base}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
            )
        except httpx.HTTPError as exc:
            return self.result(Status.OK, last_error=f"probe failed: {exc!s:.200}")

        def _int_header(name: str) -> int | None:
            raw = probe_resp.headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except ValueError:
                return None

        attrs = {
            "requests_limit": _int_header("x-ratelimit-limit-requests"),
            "requests_remaining": _int_header("x-ratelimit-remaining-requests"),
            "tokens_limit": _int_header("x-ratelimit-limit-tokens"),
            "tokens_remaining": _int_header("x-ratelimit-remaining-tokens"),
        }

        if probe_resp.status_code == 200:
            return self.result(Status.OK, **attrs)
        if probe_resp.status_code in (400, 404):
            # Key works (models.list succeeded); only the probe model is wrong.
            return self.result(
                Status.OK,
                last_error=f"probe model {model!r} unavailable: HTTP {probe_resp.status_code}",
                **attrs,
            )
        detail = probe_resp.text[:200]
        if probe_resp.status_code == 429:
            return self.result(Status.RATE_LIMITED, error=detail, **attrs)
        return self.result(
            self.classify(probe_resp.status_code),
            error=f"probe HTTP {probe_resp.status_code}: {detail}",
            **attrs,
        )
