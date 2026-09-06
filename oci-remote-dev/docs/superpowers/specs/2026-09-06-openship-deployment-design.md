# OpenShip deployment control plane

## Goal

Add a safe, opt-in way for DevVM users to install the OpenShip CLI while keeping
the always-on OpenShip control plane on a dedicated DeploymentVM. The control
plane deploys to existing servers over SSH in phase one.

## Architecture

DevVM and developer laptops remain private development environments: Nix,
agents, builds, tests, and optionally the OpenShip CLI. A dedicated Ubuntu
DeploymentVM runs the OpenShip control plane and owns GitHub webhooks,
deployment credentials, deployment history, backups, and its public dashboard.
Application targets remain separate hosts reached from the control plane using
dedicated deployment SSH keys.

OpenShip Compose mode is never co-located with the shared DevVM. It requires
Docker and Docker-socket access and binds an edge proxy on ports 80 and 443,
which conflicts with DevVM's dashboard and violates its multi-user trust
boundary.

## Repository changes

* Add `INSTALL_OPENSHIP_CLI=false` to `.env.example`, playbook defaults, and
  `scripts/deploy_config.py`.
* Add a tagged Ansible task that installs only the OpenShip CLI for each
  developer. It is opt-in and does not start a daemon or open firewall ports.
* Document the dedicated DeploymentVM bootstrap separately from DevVM's normal
  playbook. The bootstrap requires Docker, a DNS name, HTTPS, backup policy,
  and a dedicated SSH deployment identity for every target.
* Add unit tests enforcing default-off behavior and compiler/playbook/.env
  consistency. No test runs package managers, Docker, or the vendor installer.

## Failure handling and security

The per-user CLI installation must fail loudly for an explicitly requested
feature; it must not silently create a server-side control plane. The
DeploymentVM gets a narrow firewall, no developer shell accounts, encrypted
backup storage, and separate deploy keys restricted per target. Targets accept
only the deployment key and expose application traffic directly, never DevVM.

