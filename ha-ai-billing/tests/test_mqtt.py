"""MQTT discovery payloads and publishing (fake client, no broker)."""
from __future__ import annotations

import json

from app.config import AccountConfig, AppConfig, MqttConfig
from app.models import AccountResult, Status
from app.mqtt import Publisher


class FakeClient:
    def __init__(self):
        self.published: list[tuple[str, str, bool]] = []

    async def publish(self, topic, payload=None, retain=False, qos=0):
        self.published.append((topic, payload, retain))

    def topics(self):
        return [t for t, _, _ in self.published]

    def json_at(self, suffix: str):
        """Latest retained payload on a topic ending with `suffix`."""
        for topic, payload, _ in reversed(self.published):
            if topic.endswith(suffix):
                return json.loads(payload)
        raise AssertionError(f"no publish on topic ending {suffix!r}; got {self.topics()}")


def make_cfg(track=None) -> tuple[AppConfig, AccountConfig]:
    acc = AccountConfig(
        name="OpenRouter Main", provider="openrouter", api_key="sk-x-ab12", track=track or []
    )
    acc.slug = "openrouter_main"
    acc2 = AccountConfig(name="gemini", provider="gemini", api_key="g")
    acc2.slug = "gemini"
    cfg = AppConfig(mqtt=MqttConfig(host="h", port=1883), poll_interval=300, accounts=[acc, acc2])
    return cfg, acc


def ok_result(**attrs):
    base = {
        "provider": "openrouter",
        "key_hint": "ab12",
        "last_checked": "2026-10-31T00:00:00+00:00",
    }
    base.update(attrs)
    return AccountResult(Status.OK, base)


async def test_name_prefix_applied_to_display_names_only():
    cfg, acc = make_cfg(track=["balance"])
    cfg.name_prefix = "ai-billing-"
    fake = FakeClient()
    pub = Publisher(cfg)
    pub.attach(fake)
    await pub.publish_all_discovery()

    status = fake.json_at("aitokens_openrouter_main/status/config")
    assert status["name"] == "ai-billing-OpenRouter Main status"
    assert status["device"]["name"] == "ai-billing-OpenRouter Main"
    balance = fake.json_at("aitokens_openrouter_main/balance/config")
    assert balance["name"] == "ai-billing-OpenRouter Main balance"
    # identity is untouched: same unique_ids, same topics -> prefix changes
    # are pure renames in HA, never duplicates
    assert status["unique_id"] == "aitokens_openrouter_main_status"
    assert status["state_topic"] == "ai-tokens/openrouter_main/state"


async def test_no_prefix_by_default():
    cfg, _ = make_cfg()
    fake = FakeClient()
    pub = Publisher(cfg)
    pub.attach(fake)
    await pub.publish_all_discovery()
    status = fake.json_at("aitokens_gemini/status/config")
    assert status["name"] == "gemini status"
    assert status["device"]["name"] == "gemini"


async def test_discovery_payloads_shape():
    cfg, acc = make_cfg(track=["balance", "spent_month"])
    fake = FakeClient()
    pub = Publisher(cfg)
    pub.attach(fake)
    await pub.publish_all_discovery()

    status = fake.json_at("aitokens_openrouter_main/status/config")
    assert status["unique_id"] == "aitokens_openrouter_main_status"
    assert status["state_topic"] == "ai-tokens/openrouter_main/state"
    assert status["json_attributes_topic"] == "ai-tokens/openrouter_main/attributes"
    assert status["availability_topic"] == "ai-tokens/availability"
    assert status["expire_after"] == max(2 * 300, 7200) + 120
    assert status["device"]["identifiers"] == ["aitokens_openrouter_main"]
    assert status["device"]["model"] == "openrouter"

    balance = fake.json_at("aitokens_openrouter_main/balance/config")
    assert balance["device_class"] == "monetary"
    assert balance["unit_of_measurement"] == "USD"
    assert balance["state_class"] == "measurement"
    assert balance["state_topic"] == "ai-tokens/openrouter_main/balance"

    for topic, _, retain in fake.published:
        assert topic.startswith("homeassistant/sensor/aitokens_")
        assert retain is True


async def test_unique_ids_are_unique():
    cfg, _ = make_cfg(track=["balance", "spent_month"])
    fake = FakeClient()
    pub = Publisher(cfg)
    pub.attach(fake)
    await pub.publish_all_discovery()
    ids = [
        json.loads(p)["unique_id"] for t, p, _ in fake.published if t.endswith("/config")
    ]
    assert len(ids) == len(set(ids))


async def test_publish_result_state_attributes_and_tracked():
    cfg, acc = make_cfg(track=["balance"])
    fake = FakeClient()
    pub = Publisher(cfg)
    pub.attach(fake)
    await pub.publish_result(acc, ok_result(balance=16.75, currency="USD"))

    states = [(t, p) for t, p, _ in fake.published if t.endswith("/state")]
    assert states == [("ai-tokens/openrouter_main/state", "ok")]

    attrs = fake.json_at("ai-tokens/openrouter_main/attributes")
    assert attrs["provider"] == "openrouter"
    assert attrs["last_checked"]

    tracked = [(t, p) for t, p, _ in fake.published if t.endswith("/balance")]
    assert tracked == [("ai-tokens/openrouter_main/balance", "16.75")]


async def test_currency_change_republishes_discovery():
    cfg, acc = make_cfg(track=["balance"])
    fake = FakeClient()
    pub = Publisher(cfg)
    pub.attach(fake)

    await pub.publish_result(acc, ok_result(balance=99.0, currency="USD"))
    usd = fake.json_at("aitokens_openrouter_main/balance/config")
    assert usd["unit_of_measurement"] == "USD"

    await pub.publish_result(acc, ok_result(balance=110.0, currency="CNY"))
    cny = fake.json_at("aitokens_openrouter_main/balance/config")
    assert cny["unit_of_measurement"] == "CNY"


async def test_availability_and_unknown():
    cfg, acc = make_cfg()
    fake = FakeClient()
    pub = Publisher(cfg)
    pub.attach(fake)
    await pub.publish_availability(True)
    await pub.publish_unknown(acc)
    assert ("ai-tokens/availability", "online", True) in fake.published
    assert ("ai-tokens/openrouter_main/state", "unknown", True) in fake.published


async def test_tracked_field_missing_value_not_published():
    cfg, acc = make_cfg(track=["balance"])
    fake = FakeClient()
    pub = Publisher(cfg)
    pub.attach(fake)
    await pub.publish_result(acc, ok_result(currency="USD"))  # no balance
    assert not any(t.endswith("/balance") for t in fake.topics())
