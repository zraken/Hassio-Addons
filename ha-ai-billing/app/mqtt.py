"""MQTT publishing: Home Assistant discovery, state, attributes, availability.

One virtual device per account; one status sensor (state = key health) plus
optional `track` fields promoted to numeric entities in the same device."""
from __future__ import annotations

import json
import logging

from .config import AccountConfig, AppConfig
from .models import AccountResult, Status

log = logging.getLogger("mqtt")

MONETARY_FIELDS = {"balance", "spent_today", "spent_month"}
FIELD_UNITS = {
    "tokens_month": "tokens",
    "tokens_remaining": "tokens",
    "requests_remaining": "requests",
    "usage_daily_pct": "%",
    "usage_5h_pct": "%",
}
FIELD_ICONS = {
    "balance": "mdi:cash",
    "spent_today": "mdi:cash-minus",
    "spent_month": "mdi:cash-multiple",
    "tokens_month": "mdi:counter",
    "tokens_remaining": "mdi:counter",
    "requests_remaining": "mdi:server",
    "usage_daily_pct": "mdi:percent",
    "usage_5h_pct": "mdi:percent",
}


class Publisher:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.client = None  # attached once connected
        self._currency: dict[str, str] = {}  # slug -> currency last seen

    def attach(self, client) -> None:
        self.client = client

    # -- topics ---------------------------------------------------------------

    @property
    def base(self) -> str:
        return self.cfg.mqtt.base_topic

    def availability_topic(self) -> str:
        return f"{self.base}/availability"

    def state_topic(self, acc: AccountConfig) -> str:
        return f"{self.base}/{acc.slug}/state"

    def attributes_topic(self, acc: AccountConfig) -> str:
        return f"{self.base}/{acc.slug}/attributes"

    def field_topic(self, acc: AccountConfig, field: str) -> str:
        return f"{self.base}/{acc.slug}/{field}"

    # -- discovery --------------------------------------------------------------

    def _expire_after(self, acc: AccountConfig) -> int:
        # Generous: an account in auth-error backoff keeps publishing on a long
        # delay, and expire_after must not flip its state to unavailable then.
        interval = acc.effective_interval(self.cfg.poll_interval)
        return max(2 * interval, 7200) + 120

    def _device(self, acc: AccountConfig) -> dict:
        return {
            "identifiers": [f"aitokens_{acc.slug}"],
            "name": f"{self.cfg.name_prefix}{acc.name}",
            "manufacturer": "ha-ai-billing",
            "model": acc.provider,
        }

    def _availability(self) -> dict:
        return {
            "availability_topic": self.availability_topic(),
            "payload_available": "online",
            "payload_not_available": "offline",
        }

    def discovery_payloads(self, acc: AccountConfig, currency: str | None = None) -> list[tuple[str, dict]]:
        cur = (currency or self._currency.get(acc.slug) or acc.currency or "USD").upper()
        payloads: list[tuple[str, dict]] = [
            (
                f"{self.cfg.mqtt.discovery_prefix}/sensor/aitokens_{acc.slug}/status/config",
                {
                    "name": f"{self.cfg.name_prefix}{acc.name} status",
                    "unique_id": f"aitokens_{acc.slug}_status",
                    "state_topic": self.state_topic(acc),
                    "json_attributes_topic": self.attributes_topic(acc),
                    "icon": "mdi:key-variant",
                    "expire_after": self._expire_after(acc),
                    **self._availability(),
                    "device": self._device(acc),
                },
            )
        ]
        for field in acc.track:
            payload = {
                "name": f"{self.cfg.name_prefix}{acc.name} {field.replace('_', ' ')}",
                "unique_id": f"aitokens_{acc.slug}_{field}",
                "state_topic": self.field_topic(acc, field),
                "state_class": "measurement",
                "expire_after": self._expire_after(acc),
                **self._availability(),
                "device": self._device(acc),
            }
            if field in MONETARY_FIELDS:
                payload["device_class"] = "monetary"
                payload["unit_of_measurement"] = cur
            elif field in FIELD_UNITS:
                payload["unit_of_measurement"] = FIELD_UNITS[field]
            if field in FIELD_ICONS:
                payload["icon"] = FIELD_ICONS[field]
            payloads.append(
                (
                    f"{self.cfg.mqtt.discovery_prefix}/sensor/aitokens_{acc.slug}/{field}/config",
                    payload,
                )
            )
        return payloads

    async def _publish(self, topic: str, payload: str, retain: bool = True) -> None:
        await self.client.publish(topic, payload=payload, retain=retain)

    async def publish_discovery(self, acc: AccountConfig, currency: str | None = None) -> None:
        for topic, payload in self.discovery_payloads(acc, currency):
            await self._publish(topic, json.dumps(payload))
        if currency:
            self._currency[acc.slug] = currency.upper()

    async def publish_all_discovery(self) -> None:
        for acc in self.cfg.enabled_accounts:
            await self.publish_discovery(acc)

    async def publish_availability(self, online: bool) -> None:
        await self._publish(self.availability_topic(), "online" if online else "offline")

    async def publish_unknown(self, acc: AccountConfig) -> None:
        await self._publish(self.state_topic(acc), Status.UNKNOWN.value)

    # -- runtime ----------------------------------------------------------------

    async def publish_result(self, acc: AccountConfig, result: AccountResult) -> None:
        currency = (result.attributes.get("currency") or acc.currency or "USD").upper()
        if currency != self._currency.get(acc.slug):
            # Currency first learned/changed: refresh monetary entities' unit.
            await self.publish_discovery(acc, currency=currency)
        await self._publish(self.state_topic(acc), result.status.value)
        await self._publish(self.attributes_topic(acc), json.dumps(result.attributes))
        for field in acc.track:
            value = result.attributes.get(field)
            if value is not None:
                await self._publish(self.field_topic(acc, field), str(value))
