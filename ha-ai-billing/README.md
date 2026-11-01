# ha-ai-billing

One Home Assistant sensor per AI-provider API key. Reports **key health** as
the sensor state with balance/spend/quota details as attributes, using MQTT
discovery — no Home Assistant integration code, works with any broker.

```
sensor.ai_billing_openrouter_main_status   ok           (attrs: balance=9.19 USD, plan=…)
sensor.ai_billing_openai_org_spent_month   0.00 USD     (via track:)
sensor.ai_billing_gemini_status            no_credits   (prepay credits depleted — HTTP 402)
```

## Contents
- [Sensor design](#sensor-design)
- [Providers & key requirements](#providers--key-requirements)
- [Configuration (add-on)](#configuration-add-on)
- [Standalone Docker deployment](#standalone-docker-deployment)
- [MQTT behavior & lifecycle](#mqtt-behavior--lifecycle)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Development](#development)

## Sensor design

One virtual device per account (named by you), containing:

- `<name> status` — the sensor state is the key's health:

  | State | Meaning |
  |---|---|
  | `ok` | key works, provider reachable |
  | `auth_error` | key invalid, revoked, expired, or wrong key *type* (details in `last_error`) |
  | `no_credits` | key valid but balance exhausted (OpenRouter 402, Gemini prepay 402, DeepSeek `is_available=false`) |
  | `rate_limited` | 429 — transient; next poll usually recovers |
  | `error` | network/5xx/unexpected — retried with backoff |
  | `unknown` | not yet polled (startup) |

- **Uniform attributes on every status sensor**, populated only where the
  provider can report them:
  `provider, key_hint, plan, balance, currency, spent_today, spent_month,
  tokens_month, requests_remaining, requests_limit, tokens_remaining,
  tokens_limit, last_error, last_checked`
- Optional **`track`** fields promoted to real numeric sensors (history,
  long-term stats, Energy-dashboard-eligible):
  `balance, spent_today, spent_month, tokens_month, requests_remaining,
  tokens_remaining`
- Optional **`name_prefix`** (add-on option / `name_prefix:` in config.yaml /
  `NAME_PREFIX` env): prefix for all HA display names, e.g. `ai-billing-`
  gives device `ai-billing-openrouter-main` and entities like
  `sensor.ai_billing_openrouter_main_status` — one search term finds
  everything. Unique IDs are independent of the prefix, so changing it later
  renames entities in place and never duplicates them.

## Providers & key requirements

| Provider | `provider:` | Key needed | Reports |
|---|---|---|---|
| OpenRouter | `openrouter` | **management key** — `openrouter.ai/settings/management-keys` (separate page from regular keys; read-only scope suffices; keys expire → shows as `auth_error` "API key expired") | balance (USD), key plan |
| DeepSeek | `deepseek` | normal key (platform.deepseek.com) | balance (CNY/USD), sufficiency |
| OpenAI | `openai` | **admin key** (`sk-admin-…`) — `platform.openai.com/settings/organization/admin-keys`; org owner role required | spent today / month (UTC days) |
| Anthropic | `anthropic` | **Admin API key** (`sk-ant-admin…`), Claude Console → Organization; **org accounts only** | spend + tokens this month |
| Gemini | `gemini` | normal AI Studio key | key health; optional 1-token probe detects prepay-credit exhaustion (HTTP 402) |
| Meta Model API | `meta` | normal key (dev.meta.ai) | health + live rate-limit window from response headers; limits are **per team, not per key** |
| z.ai Coding Plan | `zai_plan` | GLM Coding Plan key | plan level, 5h token quota %, daily tools/MCP quota % + remaining |
| z.ai / Zhipu pay-as-you-go | `zai` | normal key | balance (**experimental**, shape-tolerant parser) |

Provider quirks worth knowing:

- **Gemini** has no usage/balance API — AI Studio's usage page is web-only.
  The optional probe (`probe_gemini`, default on) makes a 1-token
  `gemini-flash-latest` call (fractions of a cent) and surfaces Google's
  402-response when prepay credits run out. Disable for free,
  models-list-only validation.
- **OpenAI/Anthropic** have no remaining-credit endpoint; spend is what their
  admin APIs report. Organization-wide for the admin key's org.
- **Anthropic personal accounts** cannot use the Admin API at all.
- **Meta's** Muse Voice Transcribe audio-minute metering has no public API.

## Configuration (add-on)

Settings → Add-ons → **ha-ai-billing** → Configuration. Everything is the
form; nothing to edit on disk.

| Option | Meaning |
|---|---|
| `accounts` | One row per API key. `provider` dropdown, masked `api_key`, optional `track` fields (promote to numeric sensors), per-account `interval`. |
| `name_prefix` | Prefix for HA device/entity display names (e.g. `ai-billing-`) so everything is searchable with one term. |
| `mqtt_host/port/username/password` | Leave **empty** with the Mosquitto broker add-on (credentials are injected automatically by the Supervisor via `mqtt:want`). Fill in for any external broker (e.g. `mosquittos.example:8883`). |
| `mqtt_tls_insecure` | Skip broker certificate verification (self-signed/private CA on a LAN you trust). Default on; see below for the verified alternative. |
| `mqtt_ca_cert` | Path to a CA cert for proper verification, e.g. `/ssl/ca.crt` (the `/ssl` share is mapped into this add-on). |
| `probe_gemini` / `probe_meta` | 1-token liveness probes (Gemini: detects exhausted prepay credits via HTTP 402; Meta: captures rate-limit window). |
| `poll_interval` | Global poll cadence, seconds (min 30, default 300). |
| `base_topic` / `discovery_prefix` | MQTT topic roots; defaults `ai-tokens` / `homeassistant`. |
| `log_level` | Add-on log verbosity. |

**How MQTT is configured** (only this differs from standalone):

| Your broker | What you do |
|---|---|
| The **Mosquitto broker add-on** | Nothing. The Supervisor injects `MQTT_HOST/PORT/USERNAME/PASSWORD` automatically. |
| Any external broker | Fill `mqtt_host/port/username/password` in the form. |

Changing/rotating a key = edit the row → Save → the add-on restarts, re-polls
within seconds (backoff resets on restart), and a bad key shows `auth_error`
with the provider's own message while previous values stay visible.
Form-time validation is type/enum only (HA runs no add-on code on Save) — the
provider's response a few seconds later is the real verdict.

## Standalone Docker deployment

The same image runs as a plain container (this is how it can run outside HA).
The add-on's `config.yaml` filename is reserved for the HA manifest, so
standalone files live in `standalone/`:

```bash
cd ha-ai-billing/standalone
cp .env.example .env                # broker + the provider keys you use
cp config.example.yaml config.yaml  # trim accounts, set track fields, name_prefix
docker compose up -d --build
```

`.env` variables: `MQTT_HOST/PORT/USERNAME/PASSWORD`, one key per provider
(any left empty simply disables that account), plus optional `NAME_PREFIX`,
`MQTT_CA_CERT`, `MQTT_TLS_INSECURE=true` (self-signed broker cert on a
trusted LAN), `POLL_INTERVAL`, `LOG_LEVEL`.

TLS selection is automatic: TLS is used when `MQTT_CA_CERT` is set, the port
is 8883/8884, or `MQTT_TLS=true`; otherwise plaintext. With
`MQTT_TLS_INSECURE=true` the certificate is not verified (loudly logged).

## MQTT behavior & lifecycle

- Polls every `poll_interval` (default 300s, per-account `interval:` override,
  min 30s), all accounts concurrently. `ok` → normal cadence; `auth_error` →
  backoff ×4 capped at 1h (no hammering); transient errors → ×2 capped 15 min.
- Retained discovery + state; shared availability topic with Last Will:
  container death → all entities `unavailable` within seconds; broker restart
  → reconnect loop (5s→60s) and full republication; HA restart → retained
  payloads re-ingested, devices reappear populated.
- `expire_after` is sized (≥ 2h) so a backed-off account doesn't flap to
  unavailable; real staleness is covered by the availability topic.
- If a provider reports a currency different from the discovery assumption,
  discovery is re-published with the new unit automatically.
- Unique IDs and topics are stable across add-on/standalone modes and prefix
  changes — switching deployments never duplicates devices. Removing an
  account from config leaves its HA entities until deleted in HA (empty the
  discovery topic or delete via the UI).

## Troubleshooting

**Messages on the broker but no entities in HA** — in order:
1. Look in the right place: **Settings → Devices** (search `ai-billing` or the
   account name) or **Developer Tools → States** (`sensor.<name>_status`).
   MQTT-discovered devices do **not** get an integration card.
2. Settings → System → Logs → search `Invalid discovery info` / `mqtt` — HA
   drops bad payloads with a log line only.
3. MQTT integration → Configure → discovery is enabled.
4. HA's MQTT integration points at the same broker you're inspecting.
5. Restart HA (or reload the MQTT config): retained discovery is re-ingested.

**`auth_error`** → check `last_error`: 401/403 text names the problem
(`API key expired` = OpenRouter management key lapsed; `Only management keys…`
= wrong OpenRouter key type; admin-key hints for OpenAI/Anthropic; Anthropic
personal accounts get an org-required message).

**`CERTIFICATE_VERIFY_FAILED`** → self-signed broker: set
`mqtt_tls_insecure` (add-on) / `MQTT_TLS_INSECURE=true` (standalone) or
provide the CA cert.

**z.ai `plan` looks odd** → payload shapes are fixture-tested
(`tests/test_providers.py`, `ZAI_LIVE_PAYLOAD`); if z.ai changes them, the
sensor stays `ok` and `last_error` says the shape was unrecognized — please
report it.

## Security

- Add-on mode: keys live in the Supervisor's options store and reach the app
  as environment variables; the generated config references them by name —
  secrets never sit in a config file.
- Standalone mode: keys live only in `.env` (git-ignored); `config.yaml`
  references them via `!env`, never literals.
- Logs and MQTT attributes contain only the key's last 4 characters
  (`key_hint`).

## Development

```bash
cd ha-ai-billing
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # 64 tests, no network needed
python -m app.main --check               # validate config, show the plan
python -m app.main --once                # one poll of every account
```

Adding a provider: subclass `Provider` in `app/providers/`, register it in
`registry.py` (plus `KEY_NOTES`), add tests with canned responses — the
uniform-attribute test enforces the schema for you.

## Roadmap

- Aggregate `binary_sensor.ha_ai_billing_all_keys_valid` for one-glance/automation alerts.
- `gemini_gcp` provider: usage via GCP Monitoring / BigQuery billing export.
- Groq / Mistral / Together providers.
