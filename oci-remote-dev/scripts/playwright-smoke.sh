#!/usr/bin/env bash
# Offline browser smoke: load a local page and capture it with headless Chromium
# while xvfb-run supplies an isolated DISPLAY.
set -euo pipefail

output_path="${1:-${HOME}/.cache/devvm/playwright-smoke.png}"
playwright_bin="${PLAYWRIGHT_BIN:-playwright}"
xvfb_run_bin="${XVFB_RUN_BIN:-xvfb-run}"
browser_smoke_dir="$(mktemp -d)"
trap 'rm -rf "$browser_smoke_dir"' EXIT

mkdir -p "$(dirname "$output_path")"
printf '%s\n' \
  '<!doctype html><html><head><meta charset="utf-8"><title>devvm browser smoke</title></head>' \
  '<body><main><h1>Playwright + Xvfb ready</h1></main></body></html>' \
  >"$browser_smoke_dir/index.html"

export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/ms-playwright}"
"$xvfb_run_bin" -a --server-args="-screen 0 1280x720x24" \
  "$playwright_bin" screenshot --browser chromium --viewport-size="1280,720" \
  "file://$browser_smoke_dir/index.html" "$output_path"
test -s "$output_path"
printf 'Browser smoke screenshot: %s\n' "$output_path"
