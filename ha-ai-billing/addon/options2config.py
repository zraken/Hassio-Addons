#!/usr/bin/env python3
"""Convert the HA add-on's /data/options.json into app config.yaml + env.

Usage:
    options2config.py <options.json> <output_config.yaml>

Writes the generated config.yaml, then prints shell `KEY=value` lines to
stdout for run.sh to eval:

  * AITOK_KEY_<i> — one per account with a non-empty api_key (the generated
    config references them via `api_key_env`, so secrets live only in the
    environment, never in the YAML file)
  * MQTT_HOST/PORT/USERNAME/PASSWORD — ONLY when manually filled in the
    options form; when empty they are omitted so Supervisor-injected values
    (from the Mosquitto broker add-on, `mqtt:want`) survive untouched
  * MQTT_TLS_INSECURE, MQTT_CA_CERT, LOG_LEVEL — always/when set
"""
from __future__ import annotations

import json
import shlex
import sys
from typing import Any

import yaml


def _clean(value: Any) -> Any:
    """Drop empty/None entries so the generated YAML stays minimal."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v not in ("", None, [], {})}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def convert(options: dict) -> tuple[dict, list[str]]:
    exports: list[str] = []

    mqtt: dict = {
        "base_topic": options.get("base_topic", "ai-tokens"),
        "discovery_prefix": options.get("discovery_prefix", "homeassistant"),
    }
    # Manual broker settings go straight into the YAML (YAML beats env in the
    # app); when the host is left empty the whole broker block is omitted so
    # Supervisor-injected MQTT_* values flow through the app's env fallback.
    for opt, key in (
        ("mqtt_host", "host"),
        ("mqtt_port", "port"),
        ("mqtt_username", "username"),
        ("mqtt_password", "password"),
    ):
        value = options.get(opt)
        if value not in ("", None) and not (opt == "mqtt_port" and not options.get("mqtt_host")):
            mqtt[key] = value
    exports.append(f"MQTT_TLS_INSECURE={str(bool(options.get('mqtt_tls_insecure'))).lower()}")
    if options.get("log_level"):
        exports.append(f"LOG_LEVEL={shlex.quote(str(options['log_level']))}")

    accounts_out: list[dict] = []
    for i, acc in enumerate(options.get("accounts") or []):
        entry: dict = {"name": acc["name"], "provider": acc["provider"]}
        key = (acc.get("api_key") or "").strip()
        if key:
            var = f"AITOK_KEY_{i}"
            exports.append(f"{var}={shlex.quote(key)}")
            entry["api_key_env"] = var
        # Per-provider probe defaults from the global toggles (explicit wins).
        if acc.get("probe") is not None:
            entry["probe"] = bool(acc["probe"])
        elif acc["provider"] == "gemini" and not options.get("probe_gemini", True):
            entry["probe"] = False
        elif acc["provider"] == "meta" and not options.get("probe_meta", True):
            entry["probe"] = False
        for field in ("track", "interval", "probe_model", "base_url", "currency"):
            if acc.get(field) not in (None, "", []):
                entry[field] = acc[field]
        accounts_out.append(entry)

    config = {
        "mqtt": _clean(mqtt),
        "poll_interval": options.get("poll_interval", 300),
        "name_prefix": options.get("name_prefix", ""),
        "accounts": accounts_out,
    }
    return config, exports


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as fh:
        options = json.load(fh)
    config, exports = convert(options)
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, default_flow_style=False, sort_keys=False)
    print("\n".join(exports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
