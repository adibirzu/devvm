# Staging Deploy — End-to-End Confirmation

A safe, ordered procedure to confirm the agentic dev OS comes up on **real hardware**
before relying on it (or considering it for public release). Use the **`cap` staging
tenancy** (full control, disposable) — never `emdemo` (production, read-only).

> Cost note: this launches a `VM.Standard.E6.Flex` (4 OCPU / 32 GB). Tear it down with
> `destroy.sh` when done (step 8) so it doesn't accrue charges.

---

## 0. Pre-flight (tenancy safety)

Confirm you're pointed at staging, not production:

```bash
# The profile you'll deploy with must be 'cap' (staging). Verify the tenancy name:
oci iam tenancy get \
  --tenancy-id "$(awk '/^\[cap\]/{f=1} f&&/^tenancy/{print $3; exit}' ~/.oci/config)" \
  --profile cap --query 'data.name' --raw-output      # expect the staging tenancy
```

Prerequisites on the controller (your Mac):
`wireguard-tools` (`wg`), `ansible`, the `oci` CLI, an SSH keypair, and
`pip install -r requirements.txt`.

---

## 1. Configure

```bash
cd oci-remote-dev
cp .env.example .env
./scripts/setup-wizard.sh        # or edit .env directly
```

Set at minimum: `OCI_PROFILE=cap`, `OCI_REGION`, `OCI_COMPARTMENT_NAME` (or leave empty
for tenancy root), `SSH_PUBLIC_KEY_PATH`, `ADMIN_USERNAME`, `GITHUB_USER`. Leave
`MULTILLM_SOURCE_PATH` empty to clone the public MultiLLM repo (recommended).

---

## 2. Dry run (no resources created)

```bash
./scripts/deploy.sh --dry-run --profile cap --yes
```

Review the printed plan: provider/profile/region, shape, developers, WireGuard config,
open ports, toggles, and that **MultiLLM source = clone …/multillm.git**. Nothing is
created — this only previews.

---

## 3. Deploy

```bash
./scripts/deploy.sh --profile cap --yes
```

This compiles WireGuard keys, renders cloud-init, launches the VM, waits for SSH, runs
the Ansible playbook (desktops, code-servers, gateway clone+install, agent-OS,
resilience), and finishes by running `verify-agent-os` and printing its summary.

---

## 4. Connect over WireGuard

```bash
./scripts/connect.sh -u <admin_user> wg-up      # uses wg-quick (current config)
ping -c1 10.200.200.1
```

---

## 5. Verify on the VM

```bash
ssh -i <key> <admin_user>@10.200.200.1
verify-agent-os        # services + endpoints + LIVE guardrail/notify check
```

Expect: all CLIs present, services active, `:80`/`:8080`/`:8082` answer, `rm -rf /`
denied by the guardrail, notification feed writes — summary "Verification OK."

---

## 6. Smoke test the agent workflow

```bash
agentctl start claude -p demo -d ~/shared-workspace      # durable session
agentctl ls                                              # running
usage-report                                             # gateway reachable
context put "smoke" "it works" && context search smoke   # memory bus
open http://10.200.200.1/agents.html                     # live board (over VPN)
```

---

## 7. (Optional) Multi-developer + control plane

Add a `DEV_2_*` block to `.env`, re-run `deploy.sh`, and confirm the second account is
isolated (`git-whoami` as each user). Try the control-plane API:

```bash
TOKEN=$(sudo cat /etc/agent-os/admin.token)
curl http://10.200.200.1:8082/fleet/status
curl -H "X-Admin-Token: $TOKEN" -d '{"name":"carlos","ssh_key":"ssh-ed25519 AAAA…"}' \
     http://10.200.200.1:8082/developers          # → 202 queued
```

---

## 8. Teardown (don't skip on staging)

`destroy.sh` reads `OCI_PROFILE` from `.env` (set to `cap` in step 1) and prompts for
confirmation — there are no `--profile`/`--yes` flags:

```bash
./scripts/destroy.sh
# Type 'DESTROY' to confirm the OCI teardown, then 'y' to also remove the VCN/networking.
```

Confirm the instance, VCN, and security list are gone (and no stray boot volumes) in the
OCI console to avoid lingering cost.
