"""Provider behavior against canned HTTP responses (no network)."""
from __future__ import annotations

import httpx
import pytest

from app.models import ATTRIBUTE_KEYS, Status
from app.providers.anthropic import AnthropicProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.gemini import GeminiProvider
from app.providers.meta import MetaProvider
from app.providers.openai import OpenAIProvider
from app.providers.openrouter import OpenRouterProvider
from app.providers.zai import ZaiProvider
from app.providers.zai_plan import ZaiPlanProvider


def assert_uniform(result):
    """Every provider result must use only canonical attribute keys."""
    assert set(result.attributes) <= set(ATTRIBUTE_KEYS), set(result.attributes) - set(
        ATTRIBUTE_KEYS
    )
    assert result.attributes["last_checked"]
    assert result.status in set(Status)


# ------------------------------------------------------------------ openrouter


async def test_openrouter_ok(account, mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/credits"):
            return httpx.Response(
                200, json={"data": {"total_credits": 20, "total_usage": 3.25}}
            )
        if request.url.path.endswith("/auth/key"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "label": "main-key",
                        "usage": 3.25,
                        "limit": None,
                        "is_free_tier": False,
                    }
                },
            )
        return httpx.Response(404)

    result = await OpenRouterProvider(account(provider="openrouter")).fetch(
        mock_client(handler)
    )
    assert result.status is Status.OK
    assert result.attributes["balance"] == 16.75
    assert result.attributes["currency"] == "USD"
    assert "main-key" in result.attributes["plan"]
    assert_uniform(result)


async def test_openrouter_403_mentions_management_key(account, mock_client):
    result = await OpenRouterProvider(account(provider="openrouter")).fetch(
        mock_client(lambda r: httpx.Response(403, json={"error": {"message": "forbidden"}}))
    )
    assert result.status is Status.AUTH_ERROR
    assert "management" in result.attributes["last_error"].lower()


async def test_openrouter_402_no_credits(account, mock_client):
    result = await OpenRouterProvider(account(provider="openrouter")).fetch(
        mock_client(lambda r: httpx.Response(402, json={}))
    )
    assert result.status is Status.NO_CREDITS


# ------------------------------------------------------------------- deepseek


async def test_deepseek_ok(account, mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "110.00",
                        "granted_balance": "10.00",
                        "topped_up_balance": "100.00",
                    }
                ],
            },
        )

    result = await DeepSeekProvider(account(provider="deepseek")).fetch(mock_client(handler))
    assert result.status is Status.OK
    assert result.attributes["balance"] == 110.0
    assert result.attributes["currency"] == "CNY"
    assert_uniform(result)


async def test_deepseek_insufficient_balance(account, mock_client):
    result = await DeepSeekProvider(account(provider="deepseek")).fetch(
        mock_client(
            lambda r: httpx.Response(
                200,
                json={
                    "is_available": False,
                    "balance_infos": [
                        {"currency": "USD", "total_balance": "0.01", "granted_balance": "0.00", "topped_up_balance": "0.01"}
                    ],
                },
            )
        )
    )
    assert result.status is Status.NO_CREDITS
    assert result.attributes["last_error"]


async def test_deepseek_prefers_configured_currency(account, mock_client):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "is_available": True,
                "balance_infos": [
                    {"currency": "CNY", "total_balance": "700", "granted_balance": "0", "topped_up_balance": "700"},
                    {"currency": "USD", "total_balance": "99", "granted_balance": "0", "topped_up_balance": "99"},
                ],
            },
        )

    acc = account(provider="deepseek", currency="USD")
    result = await DeepSeekProvider(acc).fetch(mock_client(handler))
    assert result.attributes["balance"] == 99.0
    assert result.attributes["currency"] == "USD"


# --------------------------------------------------------------------- openai


async def test_openai_spend(account, mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        limit = request.url.params.get("limit")
        if limit == "1":  # today
            data = [{"results": [{"amount": {"value": 0.42, "currency": "usd"}}]}]
        else:  # month
            data = [
                {"results": [{"amount": {"value": 0.42, "currency": "usd"}}]},
                {"results": [{"amount": {"value": 1.08, "currency": "usd"}}]},
            ]
        return httpx.Response(200, json={"data": data, "has_more": False})

    result = await OpenAIProvider(account(provider="openai")).fetch(mock_client(handler))
    assert result.status is Status.OK
    assert result.attributes["spent_today"] == 0.42
    assert result.attributes["spent_month"] == 1.5
    assert_uniform(result)


async def test_openai_admin_key_hint(account, mock_client):
    result = await OpenAIProvider(account(provider="openai")).fetch(
        mock_client(lambda r: httpx.Response(403, json={"error": {"message": "not authorized"}}))
    )
    assert result.status is Status.AUTH_ERROR
    assert "admin" in result.attributes["last_error"].lower()


# ------------------------------------------------------------------ anthropic


async def test_anthropic_spend_and_tokens(account, mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("cost_report"):
            return httpx.Response(
                200,
                json={"data": [{"date": "2026-10-01", "amount": 1.25}, {"date": "2026-10-02", "amount": 0.5}]},
            )
        if request.url.path.endswith("usage_report/messages"):
            return httpx.Response(
                200,
                json={"data": [{"input_tokens": 1000, "output_tokens": 500}]},
            )
        return httpx.Response(404)

    result = await AnthropicProvider(account(provider="anthropic")).fetch(mock_client(handler))
    assert result.status is Status.OK
    assert result.attributes["spent_month"] == 1.75
    assert result.attributes["tokens_month"] == 1500
    assert_uniform(result)


async def test_anthropic_personal_account_hint(account, mock_client):
    result = await AnthropicProvider(account(provider="anthropic")).fetch(
        mock_client(
            lambda r: httpx.Response(
                403, json={"error": {"message": "organization does not exist"}}
            )
        )
    )
    assert result.status is Status.AUTH_ERROR
    assert "personal" in result.attributes["last_error"].lower()


# --------------------------------------------------------------------- gemini


def _gemini_handler(probe_status=200, probe_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"name": "models/gemini-flash-latest"}]})
        return httpx.Response(probe_status, json=probe_body or {})

    return handler


async def test_gemini_ok_with_probe(account, mock_client):
    result = await GeminiProvider(account(provider="gemini")).fetch(
        mock_client(_gemini_handler(200))
    )
    assert result.status is Status.OK
    assert_uniform(result)


async def test_gemini_probe_detects_no_credits(account, mock_client):
    result = await GeminiProvider(account(provider="gemini")).fetch(
        mock_client(_gemini_handler(402, {"error": {"message": "payment required"}}))
    )
    assert result.status is Status.NO_CREDITS
    assert "prepay" in result.attributes["last_error"].lower()


async def test_gemini_probe_off_is_free(account, mock_client):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"models": []})

    result = await GeminiProvider(account(provider="gemini", probe=False)).fetch(
        mock_client(handler)
    )
    assert result.status is Status.OK
    assert calls["n"] == 1  # only models.list, no inference


async def test_gemini_invalid_key(account, mock_client):
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "API key not valid. Please pass a valid API key."}})

    result = await GeminiProvider(account(provider="gemini")).fetch(mock_client(handler))
    assert result.status is Status.AUTH_ERROR


async def test_gemini_rate_limited(account, mock_client):
    result = await GeminiProvider(account(provider="gemini")).fetch(
        mock_client(_gemini_handler(429, {"error": {"message": "RESOURCE_EXHAUSTED"}}))
    )
    assert result.status is Status.RATE_LIMITED


async def test_gemini_bad_probe_model_still_ok(account, mock_client):
    result = await GeminiProvider(
        account(provider="gemini", probe_model="gemini-nope")
    ).fetch(mock_client(_gemini_handler(404)))
    assert result.status is Status.OK
    assert "probe model" in result.attributes["last_error"]


# ----------------------------------------------------------------------- meta


async def test_meta_rate_limit_headers(account, mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "muse-spark-1.3"}]})
        return httpx.Response(
            200,
            json={"choices": []},
            headers={
                "x-ratelimit-limit-requests": "3000",
                "x-ratelimit-remaining-requests": "2914",
                "x-ratelimit-limit-tokens": "4000000",
                "x-ratelimit-remaining-tokens": "3999800",
            },
        )

    result = await MetaProvider(account(provider="meta")).fetch(mock_client(handler))
    assert result.status is Status.OK
    assert result.attributes["requests_limit"] == 3000
    assert result.attributes["requests_remaining"] == 2914
    assert result.attributes["tokens_limit"] == 4000000
    assert result.attributes["tokens_remaining"] == 3999800
    assert_uniform(result)


async def test_meta_rate_limited(account, mock_client):
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    result = await MetaProvider(account(provider="meta")).fetch(mock_client(handler))
    assert result.status is Status.RATE_LIMITED


async def test_meta_bad_probe_model_still_ok(account, mock_client):
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={"error": {"message": "model not found"}})

    result = await MetaProvider(account(provider="meta", probe_model="nope")).fetch(
        mock_client(handler)
    )
    assert result.status is Status.OK
    assert "probe model" in result.attributes["last_error"]


# ------------------------------------------------------------------- z.ai ----

ZAI_LIVE_PAYLOAD = {
    "code": 200,
    "msg": "Operation successful",
    "success": True,
    "data": {
        "limits": [
            {
                "type": "TIME_LIMIT",
                "unit": 5,
                "number": 1,
                "usage": 100,
                "currentValue": 7,
                "remaining": 93,
                "percentage": 7,
                "nextResetTime": 1793890300976,
                "usageDetails": [
                    {"modelCode": "search-prime", "usage": 7},
                    {"modelCode": "web-reader", "usage": 0},
                    {"modelCode": "zread", "usage": 0},
                ],
            },
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 5, "nextResetTime": 1793556632601},
        ],
        "level": "lite",
    },
}


async def test_zai_plan_live_shape(account, mock_client):
    result = await ZaiPlanProvider(account(provider="zai_plan")).fetch(
        mock_client(lambda r: httpx.Response(200, json=ZAI_LIVE_PAYLOAD))
    )
    assert result.status is Status.OK
    plan = result.attributes["plan"]
    assert plan.startswith("lite · ")
    assert "tools 7% used" in plan
    assert "5h 5% used" in plan
    assert "resets " in plan
    assert result.attributes["requests_limit"] == 100  # TIME_LIMIT "usage" is the cap
    assert result.attributes["requests_remaining"] == 93
    assert result.attributes["usage_daily_pct"] == 7  # MCP/tools window
    assert result.attributes["usage_5h_pct"] == 5     # rolling 5h token window
    assert_uniform(result)


async def test_zai_plan_unknown_shape_stays_ok(account, mock_client):
    result = await ZaiPlanProvider(account(provider="zai_plan")).fetch(
        mock_client(lambda r: httpx.Response(200, json={"data": {"something": "else"}}))
    )
    assert result.status is Status.OK
    assert "unrecognized" in result.attributes["last_error"]


async def test_zai_balance(account, mock_client):
    result = await ZaiProvider(account(provider="zai")).fetch(
        mock_client(
            lambda r: httpx.Response(
                200, json={"data": {"available_balance": 12.5, "currency": "USD"}}
            )
        )
    )
    assert result.status is Status.OK
    assert result.attributes["balance"] == 12.5
    assert result.attributes["currency"] == "USD"


async def test_zai_unrecognized_shape(account, mock_client):
    result = await ZaiProvider(account(provider="zai")).fetch(
        mock_client(lambda r: httpx.Response(200, json={"data": {"foo": "bar"}}))
    )
    assert result.status is Status.OK
    assert "experimental" in result.attributes["last_error"]


async def test_provider_lets_network_errors_propagate(account, mock_client):
    """fetch() raises; app.main.fetch_one() is the layer that converts to Status.ERROR."""
    from app.main import fetch_one

    def handler(request):
        raise httpx.ConnectError("boom")

    client = mock_client(handler)
    acc = account(provider="openrouter")
    with pytest.raises(httpx.ConnectError):
        await OpenRouterProvider(acc).fetch(client)

    result = await fetch_one(client, acc)
    assert result.status is Status.ERROR
    assert "ConnectError" in result.attributes["last_error"]
    assert_uniform(result)


@pytest.mark.parametrize(
    "provider", ["openrouter", "deepseek", "openai", "anthropic", "gemini", "meta", "zai_plan", "zai"]
)
def test_all_providers_registered(provider):
    from app.providers.registry import PROVIDERS

    assert provider in PROVIDERS
