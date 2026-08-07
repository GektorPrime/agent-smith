#!/usr/bin/env bash
# Bootstrap and persist the Agent Smith console commands, then install a release.
set -euo pipefail

GIT_URL="https://github.com/lightspeedretail/agent_smith.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"

if ! command -v uv >/dev/null 2>&1; then
  printf 'ERROR: uv is required: curl -LsSf https://astral.sh/uv/install.sh | sh\n' >&2
  exit 1
fi

REMOTE="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
if [[ -d "$SCRIPT_DIR/wireframe" && "$REMOTE" == *"lightspeedretail/agent_smith"* ]]; then
  export AGENT_SMITH_SOURCE_DIR="$SCRIPT_DIR"
  PACKAGE="$SCRIPT_DIR"
else
  PACKAGE="git+$GIT_URL@mainframe"
fi

uv tool install --force "$PACKAGE"
TOOL_BIN="$(uv tool dir --bin)"

if [[ ":$PATH:" != *":$TOOL_BIN:"* ]]; then
  printf 'Agent Smith commands installed in %s (add it to PATH with: uv tool update-shell)\n' "$TOOL_BIN"
fi

exec "$TOOL_BIN/agent-smith-install-release" "$@"
