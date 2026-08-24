# OCI Provisioning Issue Catalog

This catalog records failures observed while provisioning and validating the VM.

## 1) OCI CLI lifecycle action misuse

- Symptom:
  - `oci compute instance action ... --action START --force` fails.
- Error:
  - `Error: No such option: --force`
- Root cause:
  - `compute instance action` does not support `--force`.
- Fix:
  - Use `oci compute instance action --instance-id <id> --action START` (no `--force`).

## 2) Commands missing required tenancy/compartment parameters

- Symptom:
  - `region-subscription list` or compartment queries fail/behave inconsistently.
- Error examples:
  - `Missing option(s) --tenancy-id.`
- Root cause:
  - Newer OCI CLI requires explicit tenancy in some paths.
- Fix:
  - Resolve tenancy OCID from profile config and pass:
    - `--tenancy-id <tenancy_ocid>` for region subscription checks.
    - `--compartment-id <tenancy_or_compartment_ocid>` for compartment listing.
- Repository changes:
  - `scripts/deploy.sh`, `scripts/setup-wizard.sh`, `scripts/status.sh`, and `scripts/destroy.sh` were updated accordingly.

## 3) False negatives caused by execution sandbox

- Symptom:
  - DNS failures in sandbox (`Could not resolve host`) while host networking is healthy.
- Root cause:
  - Network-restricted execution context produced non-representative failures.
- Fix:
  - Run OCI/SSH validation outside sandbox when real network checks are needed.

## 4) VM is STOPPED, causing SSH timeout

- Symptom:
  - `ssh ... Operation timed out`.
- Root cause:
  - Instance lifecycle state is `STOPPED`.
- Fix:
  - Check state first, then start:
    - `instance-status` -> `instance-start --wait`.

## 5) Cloud-init dev tools install failed due npm ownership

- Symptom:
  - Codex/Claude/Gemini missing on VM.
- Error:
  - `npm error EACCES ... /home/devuser/.npmrc`
- Root cause:
  - Root-owned npm artifacts prevented `devuser` npm config/install steps.
- Fix:
  - `sudo chown -R devuser:devuser /home/devuser/.npm /home/devuser/.npmrc /home/devuser/.npm-global`
  - Re-run installer: `sudo /opt/install-dev-tools.sh`

## 6) Service unit gaps after restart

- Symptom:
  - `wg-quick@wg0`, `xrdp`, or `xrdp-sesman` units not found/inactive after boot.
- Root cause:
  - Package/service provisioning partially completed or diverged after repeated recovery runs.
- Fix:
  - Validate packages first (`dpkg -l`), then re-enable/restart only existing units.
  - Prefer rebuilding from known-good image if desktop/VPN services are critically inconsistent.

## 7) External KB location unavailable

- Requested path:
  - `/dev/OCI-DEMO/KB.MD`
- Observed:
  - Path not present in this environment.
- Action:
  - This in-repo `KB/` was created to preserve operational knowledge locally.

## 8) SSH burst instability (intermittent timeout after multiple quick connects)

- Symptom:
  - First few SSH commands succeed, then subsequent commands fail with timeout for a cooldown window.
- Root cause:
  - Network-path throttling/rate-limit behavior triggered by repeated TCP handshakes.
- Fix:
  - Use SSH multiplexing (`ControlMaster`, `ControlPersist`, stable `ControlPath`) and `IdentitiesOnly=yes`.
  - Add keepalive controls (`ServerAliveInterval`, `ServerAliveCountMax`).
- Repository changes:
  - `scripts/deploy_sdk.py`, `scripts/connect.sh`, and `scripts/status.sh` now use multiplex-friendly SSH options.

## 9) Host firewall blocked browser ports despite OCI security-list allow

- Symptom:
  - Services listen on VM (`:3000`, `:3001`, `:8443`) but remote access fails.
- Root cause:
  - Oracle image iptables includes a terminal `REJECT` rule; only explicitly allowed ports pass.
- Fix:
  - Insert explicit `INPUT` accepts before reject for required ports.
  - Persist rules with `iptables-persistent` / `netfilter-persistent save`.
- Repository changes:
  - `scripts/remote-hardening.sh` applies and persists host firewall rules.

## 10) code-server exposed without authentication

- Symptom:
  - code-server reachable with `auth: none`.
- Risk:
  - Public browser IDE with no authentication.
- Fix:
  - Enforce `auth: password` in `~/.config/code-server/config.yaml`.
  - Restart `code-server@devuser`.
- Repository changes:
  - `scripts/remote-hardening.sh` enforces code-server password auth and emits generated credential output.

## 11) OCI API update/get intermittent DNS/timeout from control machine

- Symptom:
  - `oci network security-list update` and SDK `get_security_list` fail intermittently.
- Error examples:
  - `The connection to endpoint timed out.`
  - `Failed to establish a new connection: [Errno 8] nodename nor servname provided, or not known`.
- Root cause:
  - Control machine network/DNS intermittency (non-deterministic), not a deterministic API payload issue.
- Fix:
  - Retry with backoff and continue with host-level remediation when OCI-control-plane mutation is blocked.
  - Prefer SDK/CLI retries before concluding provisioning failure.

## 12) Source-restricted ingress can lock out automation runner

- Symptom:
  - SSH (`22`) and app ports become unreachable from automation machine immediately after applying narrowed CIDR rules.
- Root cause:
  - Automation source IP not included in new allow-list, or policy applied before validation from approved source.
- Fix:
  - Apply from one of the final allowed source IPs (or keep a temporary break-glass CIDR during rollout).
  - Validate with `nc`/`ssh` from each allowed IP before removing broader access.
  - Keep OCI Console access available to revert NSG/security-list rules if lockout occurs.

## 13) Helm apt mirror baltocdn.com decommissioned, domain re-registered (supply-chain)

- Symptom:
  - `Packages | Install Helm repository key` fetches `https://baltocdn.com/helm/signing.asc`, which now returns a 2-byte `OK` body or TLS errors instead of the key; the following `apt` install of `helm` fails with "no longer signed".
- Root cause:
  - Balto decommissioned the Helm apt mirror in Sept 2025; the lapsed `baltocdn.com` domain was re-registered by a third party in May 2026 and may serve malicious content (helm.sh security notice, 2026-05-29). Commit 68395f5 still pointed the arm64 keyring + repo at it.
- Fix:
  - Migrated to the current community mirror `packages.buildkite.com/helm-linux/helm-debian` with the signing-key fingerprint pinned to the value published at helm.sh/docs/intro/install (`DDF78C3E6EBB2D2CC223C95C62BA89D07698DBC6`); the run aborts on mismatch.
  - Legacy artifacts from the old domain (`/etc/apt/keyrings/helm.asc`, its sources entry) are removed before the new repo is added.
- Lesson:
  - Vendor apt-repo URLs rot and can turn hostile. When adding an apt keyring task, pin the upstream fingerprint where upstream publishes one.
