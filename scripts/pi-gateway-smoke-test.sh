#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
extension_path="$repo_root/.pi/extensions/polygate-routing/index.ts"
fixture_path="$repo_root/.pi/extensions/polygate-routing/fixtures/tool-input.txt"
pi_bin="${PI_BIN:-}"

if [[ -z "$pi_bin" ]]; then
  pi_bin="$(command -v pi 2>/dev/null || true)"
fi
if [[ -z "$pi_bin" || ! -x "$pi_bin" ]]; then
  echo "Pi executable not found. Set PI_BIN to the absolute path of pi." >&2
  exit 2
fi
if [[ ! -f "$extension_path" ]]; then
  echo "Pi extension is missing; run: git submodule update --init --recursive" >&2
  exit 2
fi

export POLYGATE_BASE_URL="${POLYGATE_BASE_URL:-http://127.0.0.1:8000/v1}"
export POLYGATE_API_KEY="${POLYGATE_API_KEY:-local-development}"
isolated_agent_dir="$(mktemp -d)"
trap 'rm -rf "$isolated_agent_dir"' EXIT

output="$({
  PI_CODING_AGENT_DIR="$isolated_agent_dir" \
    "$pi_bin" \
      --offline \
      --no-extensions \
      --extension "$extension_path" \
      --no-skills \
      --no-prompt-templates \
      --no-themes \
      --no-context-files \
      --no-session \
      --tools read \
      --model polygate/auto \
      --print \
      "Use the read tool once on POLYGATE_TEST_FILE=$fixture_path and then reply with exactly mock ok"
} 2>&1)"

if [[ "$output" != "mock ok" ]]; then
  echo "Pi Agent smoke test returned an unexpected result:" >&2
  echo "$output" >&2
  exit 1
fi

echo "Pi -> PolyGate -> Mock -> local read tool -> PolyGate -> Pi: PASS"
