#!/usr/bin/env bash
# Thin wrapper around the agent-smith-sync console script.
# All activation/deactivation logic lives in agent_smith/scripts/sync.py;
# this file only makes the installed release runnable by path:
#   bash ~/.agent-smith-tool/<version>/bin/sync.sh [args...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v uvx >/dev/null 2>&1; then
  printf 'ERROR: uvx (uv) is required: curl -LsSf https://astral.sh/uv/install.sh | sh\n' >&2
  exit 1
fi

# --from a local source tree: works offline, no git auth needed —
# the extracted release (or a dev checkout) is a complete package.
export AGENT_SMITH_INSTALL_DIR="$TOOL_ROOT"
exec uvx --from "$TOOL_ROOT" agent-smith-sync "$@"
