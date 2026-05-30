# Python SDK Runbook (OCI)

Use `scripts/oci_sdk_ops.py` for stable tenancy and instance operations.

## Prereqs

- Python `oci` package installed locally.
- Valid `~/.oci/config` profile.
- `.env.local` configured (profile, tenancy, compartment, region).

## Commands

All commands run from repo root.

### 0) Full deployment (SDK-backed)

```bash
# Non-destructive preview
./scripts/deploy.sh --dry-run --profile oci4cca --yes

# Deploy (will create/update resources)
./scripts/deploy.sh --profile oci4cca --yes
```

### 1) Validate profile and tenancy access

```bash
python3 scripts/oci_sdk_ops.py --profile oci4cca profile-check
```

### 2) Get current instance status

```bash
python3 scripts/oci_sdk_ops.py --profile oci4cca instance-status
```

### 3) Start instance and wait for RUNNING

```bash
python3 scripts/oci_sdk_ops.py --profile oci4cca instance-start --wait --wait-timeout 900
```

### 4) Stop instance and wait for STOPPED

```bash
python3 scripts/oci_sdk_ops.py --profile oci4cca instance-stop --wait --wait-timeout 900
```

### 5) Resolve primary public/private IP

```bash
python3 scripts/oci_sdk_ops.py --profile oci4cca instance-ip
```

### 6) Apply hardened remote runtime baseline

```bash
# Uses .env.local + configs/deployment-info.txt by default
./scripts/remote-hardening.sh
```

This configures:
- `code-server` password auth
- `claudecodeui` and `vibe-kanban` as persistent systemd services
- host firewall rules for `22/3000/3001/8443/51820` + persistence
- `fail2ban` sshd jail

### 7) Apply NSG + Security-List source restrictions

```bash
python3 scripts/apply_nsg_restrictions.py \
  --profile oci4cca \
  --compartment-id <compartment_ocid> \
  --instance-id <instance_ocid> \
  --vcn-id <vcn_ocid> \
  --security-list-id <security_list_ocid> \
  --source 86.122.63.0/32 \
  --source 192.168.1.100/32 \
  --port 22 \
  --port 443 \
  --port 8442 \
  --port 3000 \
  --port 3001
```

Warning: apply this from an allowed source IP or you can lock yourself out.

## Recovery Pattern

1. `profile-check`
2. `instance-status`
3. If STOPPED -> `instance-start --wait`
4. `instance-ip`
5. SSH check using `.env.local` key:
   - `ssh -i <private_key> devuser@<public_ip>`
6. Apply remote hardening:
   - `./scripts/remote-hardening.sh <public_ip>`
7. Apply source-restricted NSG policy only after confirming allow-list source IPs:
   - `python3 scripts/apply_nsg_restrictions.py ...`

## Notes

- The script reads defaults from:
  - `.env.local`
  - `configs/deployment-info.txt`
- You can override with explicit args:
  - `--instance-id`, `--tenancy-id`, `--region`, `--config-file`.
