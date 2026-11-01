"""Gemini API (Google AI Studio): key health + prepay-credit exhaustion.

No balance/usage API exists. Key validity via a free models.list call; when
`probe` is enabled (default), a 1-token generateContent call detects
HTTP 402 (prepay credits depleted) and 429 (rate limited)."""
from __future__ import annotations

import httpx

from ..models import AccountResult, Status
from .base import Provider


class GeminiProvider(Provider):
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"
    DEFAULT_PROBE_MODEL = "gemini-flash-latest"

    async def fetch(self, client: httpx.AsyncClient) -> AccountResult:
        headers = {"x-goog-api-key": self.key}

        resp = await client.get(f"{self.BASE}/models", headers=headers)
        if resp.status_code != 200:
            detail = resp.text[:200]
            if resp.status_code == 400 and "api key not valid" in resp.text.lower():
                return self.result(Status.AUTH_ERROR, error=f"invalid API key: {detail}")
            if resp.status_code == 429:
                return self.result(Status.RATE_LIMITED, error=detail)
            return self.result(
                self.classify(resp.status_code), error=f"HTTP {resp.status_code}: {detail}"
            )

        # Key is valid.
        probe = self.account.probe if self.account.probe is not None else True
        if not probe:
            return self.result(Status.OK)

        model = self.account.probe_model or self.DEFAULT_PROBE_MODEL
        payload = {
            "contents": [{"parts": [{"text": "hi"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        }
        try:
            probe_resp = await client.post(
                f"{self.BASE}/models/{model}:generateContent",
                headers=headers,
                json=payload,
            )
        except httpx.HTTPError as exc:
            return self.result(Status.OK, last_error=f"probe failed: {exc!s:.200}")

        if probe_resp.status_code == 200:
            return self.result(Status.OK)
        detail = probe_resp.text[:200]
        if probe_resp.status_code == 402:
            return self.result(
                Status.NO_CREDITS,
                error="HTTP 402 Payment Required — prepay credit balance is depleted",
            )
        if probe_resp.status_code == 429:
            return self.result(Status.RATE_LIMITED, error=detail)
        if probe_resp.status_code in (400, 404):
            # Validity already proven by models.list; note the model problem.
            return self.result(
                Status.OK, last_error=f"probe model {model!r} unavailable: HTTP {probe_resp.status_code}"
            )
        return self.result(
            self.classify(probe_resp.status_code),
            error=f"probe HTTP {probe_resp.status_code}: {detail}",
        )
