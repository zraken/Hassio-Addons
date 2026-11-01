"""Add-on wrapper: options.json -> config.yaml conversion, env fallbacks, TLS."""
from __future__ import annotations

import json
import ssl
import subprocess
import sys
from pathlib import Path

from app.config import MqttConfig, load_config
from app.main import _tls_context

REPO = Path(__file__).resolve().parent.parent
GENERATOR = REPO / "addon" / "options2config.py"

SAMPLE_OPTIONS = {
    "poll_interval": 300,
    "base_topic": "ai-tokens",
    "discovery_prefix": "homeassistant",
    "mqtt_host": "",
    "mqtt_port": 1883,
    "mqtt_username": "",
    "mqtt_password": "",
    "mqtt_ca_cert": "",
    "mqtt_tls_insecure": True,
    "probe_gemini": False,
    "probe_meta": True,
    "log_level": "info",
    "accounts": [
        {
            "name": "openrouter-main",
            "provider": "openrouter",
            "api_key": "sk-or-mgmt-test-ab12",
            "track": ["balance"],
        },
        {"name": "gemini", "provider": "gemini", "api_key": ""},  # empty -> disabled
    ],
}


def run_generator(tmp_path: Path, options: dict) -> Path:
    opts = tmp_path / "options.json"
    opts.write_text(json.dumps(options), encoding="utf-8")
    out = tmp_path / "config.gen.yaml"
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), str(opts), str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    # env export lines: KEY=value, no 'export', shell-quoted values
    for line in proc.stdout.strip().splitlines():
        key, _, value = line.partition("=")
        assert key.isidentifier() or key.startswith("AITOK")
        assert value.startswith("'") or "=" in line
    assert "sk-or-mgmt-test-ab12" in proc.stdout  # key exported via env, not yaml
    return out


def test_generated_config_roundtrip(tmp_path, monkeypatch):
    out = run_generator(tmp_path, SAMPLE_OPTIONS)
    text = out.read_text()
    assert "!env" not in text          # no raw tags in generated YAML
    assert "api_key_env: AITOK_KEY_0" in text

    monkeypatch.setenv("AITOK_KEY_0", "sk-or-mgmt-test-ab12")
    monkeypatch.delenv("AITOK_KEY_1", raising=False)
    monkeypatch.delenv("MQTT_HOST", raising=False)
    cfg = load_config(out)
    assert len(cfg.accounts) == 2
    a0, a1 = cfg.accounts
    assert a0.enabled and a0.api_key == "sk-or-mgmt-test-ab12"
    assert a0.track == ["balance"]
    # generator omits the empty key entirely -> app reports "no api_key configured"
    assert not a1.enabled and a1.disable_reason == "no api_key configured"
    # probe_gemini=false applied globally, gemini row has no explicit probe
    assert "probe" not in {k for k in _raw_accounts(out)[0]}
    # empty mqtt fields omitted -> supervisor env fallback applies
    assert cfg.mqtt.host is None


def test_generated_probe_flag_reaches_config(tmp_path, monkeypatch):
    out = run_generator(tmp_path, SAMPLE_OPTIONS)
    monkeypatch.setenv("AITOK_KEY_0", "x")
    monkeypatch.delenv("AITOK_KEY_1", raising=False)
    cfg = load_config(out)
    gemini = next(a for a in cfg.accounts if a.provider == "gemini")
    # generator emitted probe: false for gemini because probe_gemini=false
    assert gemini.probe is False


def _raw_accounts(path: Path) -> list[dict]:
    import yaml

    return yaml.safe_load(path.read_text())["accounts"]


def test_manual_mqtt_settings_in_yaml(tmp_path):
    options = dict(SAMPLE_OPTIONS)
    options.update(mqtt_host="mosquittos.example", mqtt_port=8883, mqtt_username="u", mqtt_password="p")
    out = run_generator(tmp_path, options)
    cfg_yaml = __import__("yaml").safe_load(out.read_text())
    assert cfg_yaml["mqtt"] == {
        "host": "mosquittos.example",
        "port": 8883,
        "username": "u",
        "password": "p",
        "base_topic": "ai-tokens",
        "discovery_prefix": "homeassistant",
    }


def test_name_prefix_passthrough(tmp_path):
    options = dict(SAMPLE_OPTIONS, name_prefix="ai-billing-")
    out = run_generator(tmp_path, options)
    cfg_yaml = __import__("yaml").safe_load(out.read_text())
    assert cfg_yaml["name_prefix"] == "ai-billing-"


def test_config_env_fallback_for_mqtt(tmp_path, monkeypatch):
    # All broker fields left empty in options -> omitted from YAML -> env wins.
    options = dict(SAMPLE_OPTIONS, mqtt_port="")
    out = run_generator(tmp_path, options)
    monkeypatch.setenv("AITOK_KEY_0", "x")
    monkeypatch.setenv("MQTT_HOST", "core-mosquitto")
    monkeypatch.setenv("MQTT_PORT", "1884")
    monkeypatch.setenv("MQTT_USERNAME", "addons")
    monkeypatch.setenv("MQTT_PASSWORD", "secret")
    cfg = load_config(out)
    assert cfg.mqtt.host == "core-mosquitto"
    assert cfg.mqtt.port == 1884
    assert cfg.mqtt.username == "addons"
    assert cfg.mqtt.password == "secret"


def test_yaml_mqtt_beats_env(tmp_path, monkeypatch):
    options = dict(SAMPLE_OPTIONS, mqtt_host="manual.example")
    out = run_generator(tmp_path, options)
    monkeypatch.setenv("AITOK_KEY_0", "x")
    monkeypatch.setenv("MQTT_HOST", "injected")
    cfg = load_config(out)
    assert cfg.mqtt.host == "manual.example"


def test_api_key_env_field(tmp_path, monkeypatch):
    path = tmp_path / "c.yaml"
    path.write_text(
        """
mqtt: {host: h}
accounts:
  - name: A
    provider: deepseek
    api_key_env: DEEP_KEY
"""
    )
    monkeypatch.delenv("DEEP_KEY", raising=False)
    cfg = load_config(path)
    assert not cfg.accounts[0].enabled
    assert cfg.accounts[0].disable_reason == "env DEEP_KEY is not set"

    monkeypatch.setenv("DEEP_KEY", "sk-dsk")
    cfg = load_config(path)
    assert cfg.accounts[0].enabled
    assert cfg.accounts[0].api_key == "sk-dsk"


# ------------------------------------------------------------------- TLS ----


def test_tls_plaintext_even_if_insecure(monkeypatch):
    monkeypatch.setenv("MQTT_TLS_INSECURE", "true")
    assert _tls_context(MqttConfig(host="h", port=1883)) is None


def test_tls_insecure_on_8883(monkeypatch):
    monkeypatch.setenv("MQTT_TLS_INSECURE", "true")
    ctx = _tls_context(MqttConfig(host="h", port=8883))
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_tls_verified_by_default(monkeypatch):
    monkeypatch.delenv("MQTT_TLS_INSECURE", raising=False)
    ctx = _tls_context(MqttConfig(host="h", port=8883))
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_tls_ca_cert_forces_tls(monkeypatch, tmp_path):
    monkeypatch.delenv("MQTT_TLS_INSECURE", raising=False)
    # plaintext port but explicit CA -> TLS path (returns a context or raises on bad file)
    import pytest

    ca = tmp_path / "ca.crt"
    ca.write_text("not a certificate")
    with pytest.raises(ssl.SSLError):
        _tls_context(MqttConfig(host="h", port=1883, ca_cert=str(ca)))
