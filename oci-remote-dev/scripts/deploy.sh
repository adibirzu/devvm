#!/bin/bash
# Multi-Cloud Remote Development Server Deployment Wrapper
# ========================================================
# Thin wrapper that runs the Multi-Cloud deployer.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/deploy_multicloud.py" "$@"
