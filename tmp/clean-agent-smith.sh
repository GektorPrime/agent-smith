#!/usr/bin/env bash
# Fully remove Agent Smith from this machine and this project, for a
# clean-slate reinstall test.
#
# Removes:
#   - the persistent `uv tool` install (the ~/.local/bin agent-smith-* launchers)
#   - any other agent-smith-* copies found on PATH (venv / global pip installs)
#   - the extracted release cache (~/.agent-smith-tool)
#   - the uv package cache for agent-smith
#   - this project's .agent-smith and .opencode scaffolding
#
# Usage:
#   bash clean-agent-smith.sh            # clean everything (prompts before deleting project data)
#   bash clean-agent-smith.sh --yes      # no prompts
#   bash clean-agent-smith.sh --keep-project  # leave .agent-smith / .opencode intact
#   bash clean-agent-smith.sh --force    # override a locked uv cache (uv cache clean --force)
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ASSUME_YES=0
KEEP_PROJECT=0
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=1 ;;
    --keep-project) KEEP_PROJECT=1 ;;
    --force) FORCE=1 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$1"; }

if ! command -v uv >/dev/null 2>&1; then
  warn "uv not found on PATH; uv-related steps will be skipped."
fi

# 1. Remove the persistent uv tool install.
info "Removing 'uv tool' install (agent-smith)"
if command -v uv >/dev/null 2>&1; then
  if uv tool list 2>/dev/null | grep -q '^agent-smith'; then
    uv tool uninstall agent-smith && ok "uv tool uninstalled"
  else
    ok "no uv tool install present"
  fi
fi

# 2. Remove any other agent-smith-* launchers still resolvable on PATH.
# (bash 3.2 compatible — macOS ships bash 3.2, so no `mapfile`.)
info "Checking for shadowing agent-smith-* copies on PATH"
found_any=0
while IFS= read -r launcher; do
  [[ -n "$launcher" ]] || continue
  found_any=1
  warn "still present: $launcher"
  # If it lives in a venv/framework bin, try uninstalling from that interpreter.
  bindir="$(dirname "$launcher")"
  py="$bindir/python"
  [[ -x "$py" ]] || py="$bindir/python3"
  if [[ -x "$py" ]]; then
    "$py" -m pip uninstall -y agent-smith >/dev/null 2>&1 \
      && ok "pip-uninstalled agent-smith from $py" \
      || warn "could not pip-uninstall from $py (remove $launcher manually)"
  else
    warn "no interpreter next to $launcher; remove it manually"
  fi
done < <(command -v -a agent-smith-sync agent-smith-install-release 2>/dev/null | sort -u)
[[ "$found_any" -eq 0 ]] && ok "none found on PATH"

# 3. Remove the extracted release cache.
info "Removing ~/.agent-smith-tool"
if [[ -d "$HOME/.agent-smith-tool" ]]; then
  rm -rf "$HOME/.agent-smith-tool" && ok "removed"
else
  ok "not present"
fi

# 4. Clear the uv cache for the package.
# `uv cache clean` blocks while any other uv process (e.g. a running MCP
# server started via uvx) holds the cache lock. Run it in the background
# with a timeout so this script never hangs. --force overrides the lock.
info "Clearing uv cache for agent-smith"
if command -v uv >/dev/null 2>&1; then
  if [[ "$FORCE" -eq 1 ]]; then
    uv cache clean agent-smith --force >/dev/null 2>&1 &
  else
    uv cache clean agent-smith >/dev/null 2>&1 &
  fi
  uv_pid=$!
  waited=0
  while kill -0 "$uv_pid" 2>/dev/null; do
    if [[ "$waited" -ge 15 ]]; then
      kill "$uv_pid" 2>/dev/null
      warn "cache still locked after 15s (another uv process is running)."
      warn "close running MCP servers / uvx processes and re-run, or pass --force:"
      warn "  bash clean-agent-smith.sh --force"
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$uv_pid" 2>/dev/null && ok "cache cleared"
fi

# 5. Remove this project's scaffolding.
if [[ "$KEEP_PROJECT" -eq 1 ]]; then
  info "Leaving project scaffolding intact (--keep-project)"
else
  info "Removing project scaffolding: .agent-smith and .opencode"
  targets=()
  [[ -e "$PROJECT_ROOT/.agent-smith" ]] && targets+=("$PROJECT_ROOT/.agent-smith")
  [[ -e "$PROJECT_ROOT/.opencode" ]] && targets+=("$PROJECT_ROOT/.opencode")
  if [[ ${#targets[@]} -eq 0 ]]; then
    ok "nothing to remove"
  else
    if [[ "$ASSUME_YES" -ne 1 ]]; then
      printf '  These will be deleted:\n'
      printf '    %s\n' "${targets[@]}"
      read -r -p '  Proceed? [y/N] ' reply
      [[ "$reply" =~ ^[Yy]$ ]] || { warn "skipped project cleanup"; targets=(); }
    fi
    for t in "${targets[@]}"; do
      rm -rf "$t" && ok "removed $t"
    done
  fi
fi

# 6. Verify.
info "Verification"
if command -v -a agent-smith-sync >/dev/null 2>&1; then
  warn "agent-smith-sync STILL on PATH:"
  command -v -a agent-smith-sync | sed 's/^/       /'
else
  ok "agent-smith-sync: not found"
fi
if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^agent-smith'; then
  warn "agent-smith still in 'uv tool list'"
else
  ok "no uv tool install"
fi
[[ -d "$HOME/.agent-smith-tool" ]] && warn "~/.agent-smith-tool still exists" || ok "~/.agent-smith-tool: gone"

printf '\nClean slate ready. Bootstrap a fresh install with:\n'
printf '  gh api -H "Accept: application/vnd.github.raw" \\\n'
printf '    "repos/lightspeedretail/agent_smith/contents/install.sh?ref=mainframe" | bash\n'
