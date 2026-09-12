# Cost tracking per coding harness

Two gateways, one idea: know **which coding tool** spent what. LiteLLM does
this with the client `User-Agent` header
([tutorial](https://docs.litellm.ai/docs/tutorials/cost_tracking_coding));
the devvm MultiLLM pipeline mirrors it with a `harness` dimension.

## LiteLLM gateways (adi1 primary / adi2 standby)

- LiteLLM ≥ 1.73 records the incoming `User-Agent` as a spend-log tag
  **automatically** — no proxy config required. Do **not** set
  `litellm_settings.disable_add_user_agent_to_request_tags` (it defaults to
  false; setting it to true turns tracking off).
- Each coding CLI sends its own vendor User-Agent; LiteLLM records it
  verbatim, so per-tool cost and daily/weekly/monthly active users fall out
  of the Logs / Usage dashboard with zero harness-side changes.
- View: Admin UI → Logs (User-Agent tag per request), Usage dashboard
  (cost per tool), or `GET /spend/logs` for raw rows.
- Verify a harness is tracked (run against the Tailnet endpoint):

  ```bash
  curl -X POST https://adi1.tailb55406.ts.net/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LITELLM_KEY" \
    -H "User-Agent: claude-cli/1.0" \
    -d '{"model": "qwen3.8-flash-next",
         "messages": [{"role": "user", "content": "ping"}]}'
  ```

  then confirm the request appears in Logs tagged with that User-Agent.
- The live proxy config is private (`~/.qwen38fn/monitor` on adi1, never in
  Git); the tracked example template is
  `runtime/litellm-config.example.yaml` in dgx-spark-observability.
- For per-developer attribution on top of per-tool tracking, issue one
  LiteLLM virtual key per developer (`user_id` set) — spend then slices by
  both key owner and User-Agent.

## MultiLLM gateway (devvm OCI VM)

The custom gateway has no HTTP-proxy spend log, so the `harness` dimension
is carried explicitly:

- **Canonical names** (firstmate spawn list + devvm extras): `claude`,
  `codex`, `gemini`, `opencode`, `pi`, `pi-signed`, `grok`, `kimi`,
  `cursor`, `cursor-agent`, `muse`, `agy`, `cline`, `copilot`, `aider`.
  Anything else normalizes to `other`.
- **Normalization** (`normalize_harness` in `scripts/usage_report.py` and
  `scripts/agent_status.py`, duplicated because each CLI installs
  standalone): lowercase, tokenize on non-alphanumerics, match whole
  tokens — so `claude-cli/1.0` → `claude` while `copilot` does **not**
  collapse to `pi`; `cursor-agent` / `pi-signed` match as hyphenated
  wholes first.
- **Collector contract**: the per-user `multillm-collect` run reads each
  CLI's home-directory stats and pushes snapshots tagged with tenant
  (developer) and harness. Harnesses without a parseable local stat store
  still appear as live sessions (via `agentctl` metadata) with no cost
  until the gateway learns their format.
- **Surfaces**:
  - `usage-report --team` prints "By coding harness" when the gateway
    serves `by_harness`; `usage-report --team --harness <name>` filters
    to one harness. Both stay silent on older gateways (no `by_harness`
    key → no section, no error).
  - The agent board (`agent-status` → `agents.json` → `dashboard/agents.html`)
    always shows live sessions per harness (`by_harness`: sessions +
    active) and joins per-harness cost when the gateway serves it.

## Harness map

| Harness | devvm provisioning | LiteLLM User-Agent behavior |
| --- | --- | --- |
| claude | global npm `claude-code` | vendor UA recorded verbatim |
| codex | global npm `@openai/codex` | vendor UA recorded verbatim |
| gemini | global npm `@google/gemini-cli` | vendor UA recorded verbatim |
| opencode | global npm `opencode-ai` | vendor UA recorded verbatim |
| pi / pi-signed | global npm (+ `FM_PI_HARNESS`) | vendor UA recorded verbatim |
| grok | per-user vendor installer | vendor UA recorded verbatim |
| kimi | global npm `kimi-code` (bin `kimi`) | vendor UA recorded verbatim |
| cursor / cursor-agent | per-user installer | vendor UA recorded verbatim |
| muse | harness-detected (no installer) | vendor UA recorded verbatim |
| agy (Antigravity) | opt-in `INSTALL_ANTIGRAVITY` | vendor UA recorded verbatim |
| cline | global npm `cline` | vendor UA recorded verbatim |
| copilot | global npm `@github/copilot` | vendor UA recorded verbatim |
| aider | via `agentctl start aider` | vendor UA recorded verbatim |
| ori / openrouter | routers wrapping the above CLIs (`ori claude`, …) | tracked under the wrapped CLI's UA |

`User-Agent` is client-asserted, so treat it as an analytics dimension, not
an auth boundary. Where a hard boundary matters (memory/context bus), devvm
uses the authenticated `X-MultiLLM-Tenant` header instead.
