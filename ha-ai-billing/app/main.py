"""Entry point.

Modes:
  default  — connect MQTT, announce discovery, poll forever
  --check  — validate config and print the plan (no network, no MQTT)
  --once   — poll every enabled account once, print results to stdout
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import ssl
import sys
from pathlib import Path

import httpx

from .config import AccountConfig, AppConfig, ConfigError, load_config, load_dotenv
from .models import AccountResult, Status
from .mqtt import Publisher
from .providers.base import now_iso
from .providers.registry import KEY_NOTES, get_provider

log = logging.getLogger("ha-ai-billing")


# ---------------------------------------------------------------- summary ----


def print_summary(cfg: AppConfig) -> None:
    m = cfg.mqtt
    print("Configuration summary")
    print(f"  broker        : {m.host or '<not set>'}:{m.port}  (base_topic: {m.base_topic})")
    print(f"  discovery     : {m.discovery_prefix}/sensor/aitokens_<account>/...")
    print(f"  poll interval : {cfg.poll_interval}s")
    print(f"  accounts      : {len(cfg.enabled_accounts)} enabled / {len(cfg.accounts)} configured")
    for acc in cfg.accounts:
        if acc.enabled:
            entities = ["status", *(f"{f} (tracked)" for f in acc.track)]
            print(f"    [ok]   {acc.name} ({acc.provider}) key: {KEY_NOTES[acc.provider]}")
            print(f"           entities: {', '.join(entities)}; interval: {acc.effective_interval(cfg.poll_interval)}s")
        else:
            print(f"    [skip] {acc.name} ({acc.provider}) — disabled: {acc.disable_reason}")


# ------------------------------------------------------------------ fetch ----


async def fetch_one(http: httpx.AsyncClient, acc: AccountConfig) -> AccountResult:
    try:
        return await get_provider(acc).fetch(http)
    except Exception as exc:  # noqa: BLE001 - a broken provider must not kill the loop
        return AccountResult(
            Status.ERROR,
            {
                "provider": acc.provider,
                "last_error": f"{type(exc).__name__}: {exc!s}"[:250],
                "last_checked": now_iso(),
            },
        )


async def run_once(cfg: AppConfig) -> int:
    if not cfg.enabled_accounts:
        print("No enabled accounts (all api_key env vars unset?). Nothing to do.")
        return 0
    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True)
    try:
        results = await asyncio.gather(*(fetch_one(http, a) for a in cfg.enabled_accounts))
    finally:
        await http.aclose()

    print(f"{'account':<24} {'provider':<12} {'status':<14} detail")
    print("-" * 100)
    for acc, result in zip(cfg.enabled_accounts, results):
        attrs = result.attributes
        bits = []
        for key in ("balance", "currency", "spent_today", "spent_month", "plan",
                    "requests_remaining", "tokens_remaining"):
            if attrs.get(key) is not None:
                bits.append(f"{key}={attrs[key]}")
        if result.status is not Status.OK and attrs.get("last_error"):
            bits.append(f"error: {attrs['last_error']}")
        print(f"{acc.name:<24} {acc.provider:<12} {result.status.value:<14} {' '.join(bits)}")
    return 0


# ------------------------------------------------------------------- loop ----


async def _interruptible_sleep(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass


async def account_loop(
    acc: AccountConfig,
    cfg: AppConfig,
    publisher: Publisher,
    http: httpx.AsyncClient,
    stop: asyncio.Event,
) -> None:
    interval = acc.effective_interval(cfg.poll_interval)
    delay = min(interval, 120)  # backoff starts from here, resets on success
    while not stop.is_set():
        result = await fetch_one(http, acc)
        try:
            await publisher.publish_result(acc, result)
        except Exception:  # noqa: BLE001
            log.exception("publish failed for account %s", acc.name)
        detail = result.attributes.get("last_error") or ""
        log.info("%s: %s %s", acc.name, result.status.value, detail)
        if result.status is Status.OK:
            delay = interval
        elif result.status is Status.AUTH_ERROR:
            delay = min(delay * 4, 3600)
        else:
            delay = min(delay * 2, 900)
        await _interruptible_sleep(stop, delay)


def _tls_context(m):
    ca = m.ca_cert or os.environ.get("MQTT_CA_CERT")
    insecure = os.environ.get("MQTT_TLS_INSECURE", "").strip().lower() in ("1", "true", "yes")
    use_tls = bool(ca) or m.port in (8883, 8884) or os.environ.get("MQTT_TLS") == "true"
    if not use_tls:
        return None  # plaintext (e.g. supervisor-injected Mosquitto add-on on 1883)
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        log.warning(
            "MQTT_TLS_INSECURE is enabled — broker certificate verification is DISABLED"
        )
        return ctx
    if ca:
        return ssl.create_default_context(cafile=os.path.expanduser(ca))
    return ssl.create_default_context()


async def serve(cfg: AppConfig) -> int:
    import aiomqtt  # imported late: --check/--once work without the broker libs

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    publisher = Publisher(cfg)
    m = cfg.mqtt
    tls = _tls_context(m)
    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True)
    backoff = 5
    tasks: list[asyncio.Task] = []

    try:
        while not stop.is_set():
            kwargs: dict = {
                "hostname": m.host,
                "port": m.port,
                "will": aiomqtt.Will(publisher.availability_topic(), payload=b"offline", retain=True),
            }
            if m.username:
                kwargs["username"] = m.username
            if m.password:
                kwargs["password"] = m.password
            if tls:
                kwargs["tls_context"] = tls
            try:
                async with aiomqtt.Client(**kwargs) as client:
                    log.info("connected to mqtt://%s:%s", m.host, m.port)
                    backoff = 5
                    publisher.attach(client)
                    await publisher.publish_availability(True)
                    await publisher.publish_all_discovery()
                    for acc in cfg.enabled_accounts:
                        await publisher.publish_unknown(acc)
                    if not cfg.enabled_accounts:
                        log.warning("no enabled accounts — set api key env vars and restart")
                    tasks = [
                        asyncio.create_task(account_loop(a, cfg, publisher, http, stop))
                        for a in cfg.enabled_accounts
                    ]
                    await stop.wait()
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks = []
                    await publisher.publish_availability(False)
            except aiomqtt.MqttError as exc:
                if stop.is_set():
                    break
                log.warning("MQTT connection failed: %s — retrying in %ss", exc, backoff)
                await _interruptible_sleep(stop, backoff)
                backoff = min(backoff * 2, 60)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await http.aclose()
    return 0


# ------------------------------------------------------------------- main ----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ha-ai-billing", description=__doc__)
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_PATH", "config.yaml"),
        help="path to config.yaml (default: $CONFIG_PATH or ./config.yaml)",
    )
    parser.add_argument("--check", action="store_true", help="validate config and print the plan, then exit")
    parser.add_argument("--once", action="store_true", help="poll every enabled account once and print results")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="logging level (default: $LOG_LEVEL or INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # per-request lines are noise

    load_dotenv(Path(".env"))

    try:
        cfg = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print_summary(cfg)
        if not cfg.mqtt.host:
            print("\nnote: mqtt.host is not set (MQTT_HOST) — required for normal startup")
        return 0

    if args.once:
        return asyncio.run(run_once(cfg))

    if not cfg.mqtt.host:
        print("ERROR: mqtt.host is not set — set MQTT_HOST in .env", file=sys.stderr)
        return 2

    try:
        return asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
