"""Config loading: !env interpolation, validation, slugs, dotenv."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import (
    ConfigError,
    load_config,
    load_dotenv,
    parse_config,
    slugify,
)


def write_cfg(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


BASIC = """
mqtt:
  host: !env MQTT_HOST
  port: !env MQTT_PORT
accounts:
  - name: A
    provider: openrouter
    api_key: !env OR_KEY
"""


def test_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "broker.local")
    monkeypatch.setenv("MQTT_PORT", "1883")
    monkeypatch.setenv("OR_KEY", "sk-or-1")
    cfg = load_config(write_cfg(tmp_path, BASIC))
    assert cfg.mqtt.host == "broker.local"
    assert cfg.mqtt.port == 1883
    assert cfg.accounts[0].api_key == "sk-or-1"
    assert cfg.accounts[0].enabled


def test_missing_env_disables_account_and_names_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "broker.local")
    monkeypatch.setenv("MQTT_PORT", "1883")
    monkeypatch.delenv("OR_KEY", raising=False)
    cfg = load_config(write_cfg(tmp_path, BASIC))
    acc = cfg.accounts[0]
    assert not acc.enabled
    assert acc.disable_reason == "env OR_KEY is not set"
    assert cfg.enabled_accounts == []


def test_empty_env_value_counts_as_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "broker.local")
    monkeypatch.setenv("MQTT_PORT", "1883")
    monkeypatch.setenv("OR_KEY", "")  # .env.example placeholder: empty = unset
    cfg = load_config(write_cfg(tmp_path, BASIC))
    assert not cfg.accounts[0].enabled


def test_missing_mqtt_host_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.delenv("MQTT_HOST", raising=False)
    monkeypatch.setenv("MQTT_PORT", "1883")
    monkeypatch.setenv("OR_KEY", "k")
    cfg = load_config(write_cfg(tmp_path, BASIC))
    assert cfg.mqtt.host is None  # only fatal at serve() time


def test_unknown_provider_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown provider"):
        load_config(
            write_cfg(
                tmp_path,
                """
mqtt: {host: h}
accounts:
  - name: A
    provider: nope
    api_key: k
""",
            )
        )


def test_duplicate_slug_rejected(tmp_path):
    with pytest.raises(ConfigError, match="collides"):
        load_config(
            write_cfg(
                tmp_path,
                """
mqtt: {host: h}
accounts:
  - name: my acc
    provider: openrouter
    api_key: k
  - name: My-Acc
    provider: deepseek
    api_key: k
""",
            )
        )


def test_untrackable_field_rejected(tmp_path):
    with pytest.raises(ConfigError, match="untrackable"):
        load_config(
            write_cfg(
                tmp_path,
                """
mqtt: {host: h}
accounts:
  - name: A
    provider: openrouter
    api_key: k
    track: [key_hint]
""",
            )
        )


def test_unknown_account_key_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown keys"):
        load_config(
            write_cfg(
                tmp_path,
                """
mqtt: {host: h}
accounts:
  - name: A
    provider: openrouter
    api_key: k
    apikey: k
""",
            )
        )


def test_poll_interval_floor(tmp_path):
    cfg = load_config(
        write_cfg(
            tmp_path,
            """
mqtt: {host: h}
poll_interval: 5
accounts: []
""",
        )
    )
    assert cfg.poll_interval == 30


def test_slugify():
    assert slugify("OpenRouter Main") == "openrouter_main"
    assert slugify("  z.ai–GLM (plan) ") == "z_aiglm_plan"  # en-dash is dropped, not dashed
    assert slugify("Café Ø") == "cafe"
    assert slugify("!!!") == "account"


def test_parse_config_rejects_non_dict_accounts():
    with pytest.raises(ConfigError):
        parse_config({"mqtt": {"host": "h"}, "accounts": "nope"})


def test_name_prefix_from_yaml_and_env(tmp_path, monkeypatch):
    cfg = load_config(
        write_cfg(
            tmp_path,
            """
mqtt: {host: h}
name_prefix: yaml-prefix-
accounts: []
""",
        )
    )
    assert cfg.name_prefix == "yaml-prefix-"

    monkeypatch.setenv("NAME_PREFIX", "env-prefix-")
    cfg = load_config(
        write_cfg(
            tmp_path,
            """
mqtt: {host: h}
accounts: []
""",
        )
    )
    assert cfg.name_prefix == "env-prefix-"

    # YAML wins over env
    cfg = load_config(
        write_cfg(
            tmp_path,
            """
mqtt: {host: h}
name_prefix: yaml-prefix-
accounts: []
""",
        )
    )
    assert cfg.name_prefix == "yaml-prefix-"


def test_load_dotenv_no_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "TEST_AAA=from_file\n"
        'TEST_BBB="quoted"\n'
        "TEST_EMPTY=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_AAA", "from_env")
    monkeypatch.delenv("TEST_BBB", raising=False)
    load_dotenv(env_file)
    import os

    assert os.environ["TEST_AAA"] == "from_env"  # real env wins
    assert os.environ["TEST_BBB"] == "quoted"
