"""Configuration: YAML with ``!env VAR`` interpolation, validation, slugs."""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import TRACKABLE_FIELDS


class ConfigError(Exception):
    """Raised when the configuration file is invalid."""


class MissingEnv:
    """Sentinel returned for a ``!env`` variable that is unset or empty."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<unset env {self.name}>"


class _EnvLoader(yaml.SafeLoader):
    pass


def _env_constructor(loader: yaml.Loader, node: yaml.Node):
    name = loader.construct_scalar(node).strip()
    value = os.environ.get(name)
    return value if value not in (None, "") else MissingEnv(name)


_EnvLoader.add_constructor("!env", _env_constructor)


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, never overrides real env vars.

    Values are intentionally never logged."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "account"


@dataclass
class MqttConfig:
    host: str | None = None
    port: int = 1883
    username: str | None = None
    password: str | None = None
    base_topic: str = "ai-tokens"
    discovery_prefix: str = "homeassistant"
    ca_cert: str | None = None


@dataclass
class AccountConfig:
    name: str
    provider: str
    api_key: str | None = None
    track: list[str] = field(default_factory=list)
    interval: int | None = None  # per-account poll interval override (seconds)
    probe: bool | None = None  # provider-specific default if None
    probe_model: str | None = None
    base_url: str | None = None
    currency: str = "USD"
    # Filled by the loader:
    slug: str = ""
    enabled: bool = True
    disable_reason: str | None = None

    def effective_interval(self, default: int) -> int:
        return int(self.interval or default)


ACCOUNT_FIELDS = {
    "name",
    "provider",
    "api_key",
    "api_key_env",
    "track",
    "interval",
    "probe",
    "probe_model",
    "base_url",
    "currency",
}


@dataclass
class AppConfig:
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    poll_interval: int = 300
    name_prefix: str = ""  # HA display-name prefix, e.g. "ai-billing-"
    accounts: list[AccountConfig] = field(default_factory=list)

    @property
    def enabled_accounts(self) -> list[AccountConfig]:
        return [a for a in self.accounts if a.enabled]


def _coerce_int(value, what: str, errors: list[str]) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        errors.append(f"{what}: expected an integer, got {value!r}")
        return None


def parse_config(raw: dict) -> AppConfig:
    from .providers.registry import PROVIDERS  # local import: registry needs providers

    errors: list[str] = []

    unknown_top = set(raw) - {"mqtt", "poll_interval", "name_prefix", "accounts"}
    if unknown_top:
        errors.append(f"unknown top-level keys: {sorted(unknown_top)}")

    cfg = AppConfig()

    mqtt_raw = raw.get("mqtt") or {}
    if not isinstance(mqtt_raw, dict):
        errors.append("mqtt: expected a mapping")
        mqtt_raw = {}
    unknown_mqtt = set(mqtt_raw) - {
        "host",
        "port",
        "username",
        "password",
        "base_topic",
        "discovery_prefix",
        "ca_cert",
    }
    if unknown_mqtt:
        errors.append(f"mqtt: unknown keys {sorted(unknown_mqtt)}")

    def _mqtt_value(key: str):
        value = mqtt_raw.get(key)
        if isinstance(value, MissingEnv):
            # host/port/credentials are all lenient at parse time: --check and
            # --once work without a broker; serve() enforces host explicitly.
            return None
        return value

    port = _mqtt_value("port")
    port = _coerce_int(port, "mqtt.port", errors) if port is not None else 1883
    cfg.mqtt = MqttConfig(
        host=_mqtt_value("host") or None,
        port=port or 1883,
        username=_mqtt_value("username") or None,
        password=_mqtt_value("password") or None,
        base_topic=str(_mqtt_value("base_topic") or "ai-tokens"),
        discovery_prefix=str(_mqtt_value("discovery_prefix") or "homeassistant"),
        ca_cert=_mqtt_value("ca_cert") or None,
    )
    # Env fallback for anything the YAML left unset — this is how the HA
    # Supervisor's injected MQTT_* variables (from `mqtt:want` + the Mosquitto
    # broker add-on) reach the app when the add-on's generated config omits
    # the broker fields. YAML values always win.
    if cfg.mqtt.host is None:
        cfg.mqtt.host = os.environ.get("MQTT_HOST") or None
    if "port" not in mqtt_raw and os.environ.get("MQTT_PORT"):
        try:
            cfg.mqtt.port = int(os.environ["MQTT_PORT"])
        except ValueError:
            errors.append(f"mqtt.port env: invalid integer {os.environ['MQTT_PORT']!r}")
    if cfg.mqtt.username is None:
        cfg.mqtt.username = os.environ.get("MQTT_USERNAME") or None
    if cfg.mqtt.password is None:
        cfg.mqtt.password = os.environ.get("MQTT_PASSWORD") or None
    if cfg.mqtt.ca_cert is None:
        cfg.mqtt.ca_cert = os.environ.get("MQTT_CA_CERT") or None

    interval = raw.get("poll_interval", 300)
    if isinstance(interval, MissingEnv):
        interval = 300
    else:
        interval = _coerce_int(interval, "poll_interval", errors) or 300
    cfg.poll_interval = max(30, interval)

    prefix = raw.get("name_prefix", "")
    if isinstance(prefix, MissingEnv):
        prefix = ""
    cfg.name_prefix = str(prefix).strip() or os.environ.get("NAME_PREFIX", "").strip()

    accounts_raw = raw.get("accounts") or []
    if not isinstance(accounts_raw, list):
        errors.append("accounts: expected a list")
        accounts_raw = []

    seen_names: set[str] = set()
    seen_slugs: dict[str, str] = {}
    for idx, item in enumerate(accounts_raw):
        where = f"accounts[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        unknown = set(item) - ACCOUNT_FIELDS
        if unknown:
            errors.append(f"{where}: unknown keys {sorted(unknown)}")
        name = item.get("name")
        provider = item.get("provider")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}: 'name' is required")
            continue
        if not isinstance(provider, str) or provider not in PROVIDERS:
            known = ", ".join(sorted(PROVIDERS))
            errors.append(f"{where} ({name}): unknown provider {provider!r} — known: {known}")
            continue
        if name in seen_names:
            errors.append(f"{where}: duplicate account name {name!r}")
            continue
        seen_names.add(name)

        acc = AccountConfig(name=name.strip(), provider=provider)
        acc.slug = slugify(acc.name)
        if acc.slug in seen_slugs:
            errors.append(
                f"{where}: account name {name!r} collides with {seen_slugs[acc.slug]!r} "
                f"after slugification (both become {acc.slug!r})"
            )
            continue
        seen_slugs[acc.slug] = acc.name

        api_key = item.get("api_key")
        api_key_env = item.get("api_key_env")
        if isinstance(api_key, MissingEnv):
            acc.api_key = None
            acc.enabled = False
            acc.disable_reason = f"env {api_key.name} is not set"
        elif isinstance(api_key, str) and api_key.strip():
            acc.api_key = api_key.strip()
        elif isinstance(api_key_env, str) and api_key_env.strip():
            # Alternative to !env: name the environment variable directly
            # (used by the add-on's generated config).
            acc.api_key = os.environ.get(api_key_env.strip()) or None
            if not acc.api_key:
                acc.enabled = False
                acc.disable_reason = f"env {api_key_env.strip()} is not set"
        elif api_key is None:
            acc.enabled = False
            acc.disable_reason = "no api_key configured"
        else:
            acc.enabled = False
            acc.disable_reason = "api_key is empty"

        track = item.get("track") or []
        if not isinstance(track, list):
            errors.append(f"{where} ({name}): 'track' must be a list")
        else:
            bad = [t for t in track if t not in TRACKABLE_FIELDS]
            if bad:
                errors.append(
                    f"{where} ({name}): untrackable fields {bad} — "
                    f"trackable: {sorted(TRACKABLE_FIELDS)}"
                )
            acc.track = list(track)

        if item.get("interval") is not None:
            iv = _coerce_int(item["interval"], f"{where} ({name}).interval", errors)
            if iv is not None and iv < 30:
                errors.append(f"{where} ({name}): interval must be >= 30s")
            acc.interval = iv

        if item.get("probe") is not None:
            acc.probe = bool(item["probe"])
        acc.probe_model = item.get("probe_model") or None
        acc.base_url = item.get("base_url") or None
        acc.currency = str(item.get("currency") or "USD").upper()

        cfg.accounts.append(acc)

    if errors:
        raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(errors))
    return cfg


def load_config(path: Path) -> AppConfig:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_EnvLoader)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("config file must contain a YAML mapping at the top level")
    return parse_config(raw)
