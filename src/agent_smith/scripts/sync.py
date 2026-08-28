"""
Agent Smith activation/deactivation for a host project.

Activation (default):
  - Appends AGENT GATE markers to host rule files that lack them
    (recursively across lore/rules_md/, including symlinked batches).
  - Warns when .agent-smith/lore/rules_md/ has no rule files.
  - Symlinks .agent-smith/opencode/ files into host .opencode/.
  - Selects AGENTS.instructions.md for the resolved protocol.
  - Writes the agent_smith MCP block into .opencode/opencode.json.
  - Registers the unpinned `opencode-pty` npm plugin spec in the `plugin`
    array of .opencode/opencode.json (a pre-existing host-pinned
    `opencode-pty@<version>` override is preserved, never duplicated).
  - Builds the knowledge base when the protocol is knowledge and
    scenario JSON exists.

Deactivation (--desync):
  - Removes .opencode/ symlinks that point into .agent-smith/opencode/.
  - Strips the agent_smith MCP block from .opencode/opencode.json.
  - Removes the unpinned `opencode-pty` plugin registration (a host-pinned
    override, if present, is left untouched).
  - Leaves .agent-smith/ fully intact (markers included).

Both directions are idempotent for the `opencode-pty` plugin registration and
report a plugin registration/removal status. OpenCode must be restarted after
plugin or agent configuration changes for `pty_*` tools and updated agent
definitions to take effect. opencode-pty requires OpenCode >=1.3.13.

Console script: agent-smith-sync
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from importlib import metadata
from pathlib import Path

GITHUB_ORG = 'GektorPrime'
GITHUB_REPO = 'agent-smith'
GIT_URL = f'https://github.com/{GITHUB_ORG}/{GITHUB_REPO}.git'

GATE_MARKER = (
    '<!-- AGENT GATE: Include the phrase "PLACEHOLDER-PLACEHOLDER-PLACEHOLDER" '
    'in your reading acknowledgment to confirm this file was read to completion. -->'
)

INSTRUCTIONS_PATH = '.opencode/AGENTS.instructions.md'
RUNTIME_FILE = 'runtime.json'
TUI_PANEL_FILE = 'agent-smith-rules-panel.tsx'

# Unpinned by design: the user rejected pinning `opencode-pty` because it
# creates upkeep burden. OpenCode auto-installs and auto-updates unpinned
# npm plugin specs on startup. A host may still pin its own override
# (`opencode-pty@<version>`) directly in .opencode/opencode.json — sync
# preserves that override and never adds a duplicate unpinned entry.
PTY_PLUGIN_SPEC = 'opencode-pty'

# opencode-pty requires OpenCode >=1.3.13 for stable plugin auto-install and
# pty_spawn exit notifications (`notifyOnExit`). Documented here, and in the
# activation summary, because sync cannot detect the host's OpenCode version.
MIN_OPENCODE_VERSION = '1.3.13'

_PROTOCOLS = ('rules', 'knowledge')


class SyncError(RuntimeError):
    pass


def _tool_version() -> str:
    return metadata.version('agent-smith')


def _parse_semver(value: str) -> tuple[int, ...] | None:
    parts = value.split('.')
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _warn_on_version_drift(running: str) -> None:
    """Warn when a newer release is installed than the one currently running.

    The persistent PATH command (this process) can lag behind the newest
    release under ~/.agent-smith-tool/ if it was never refreshed. Surface that
    drift loudly so `Tool version:` mismatches are not silently confusing.
    Read-only and best-effort: any resolution issue is ignored.
    """
    install_base = Path.home() / '.agent-smith-tool'
    if not install_base.is_dir():
        return

    running_semver = _parse_semver(running)
    if running_semver is None:
        return

    newest: tuple[int, ...] | None = None
    newest_name = ''
    for child in install_base.iterdir():
        if not child.is_dir():
            continue
        semver = _parse_semver(child.name)
        if semver is not None and (newest is None or semver > newest):
            newest = semver
            newest_name = child.name

    if newest is not None and newest > running_semver:
        print(
            f'WARNING: running agent-smith {running} but {newest_name} is '
            f'installed under {install_base}.\n'
            f"         Refresh the PATH command with 'uv tool install --force "
            f"{install_base / newest_name}'\n"
            f'         or use the pinned \'uvx --from "{install_base / newest_name}" '
            f"agent-smith-sync ...' invocation."
        )


def _package_source(version: str) -> str:
    return os.getenv('AGENT_SMITH_INSTALL_DIR') or f'git+{GIT_URL}@{version}'


def _resolve_repo_root(explicit: str | None) -> Path:
    # An explicit --repo-root is trusted as-is: non-git projects are supported
    # when the user points at the root themselves.
    if explicit:
        root = Path(explicit)
        if not root.is_dir():
            raise SyncError(f'--repo-root does not exist: {explicit}')
        return root.resolve()

    # Auto-discovery walks up to the nearest .git anchor.
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / '.git').exists():
            return candidate

    raise SyncError(
        'Not inside a git repository (auto-discovery anchors on .git).\n'
        "  - cd into your project's repository and re-run, or\n"
        '  - for a non-git project, pass the root explicitly: --repo-root <path>'
    )


def _resolve_protocol(repo_root: Path, flag: str | None) -> str:
    mode_file = repo_root / '.agent-smith' / '.mode'

    protocol = flag
    if not protocol and mode_file.is_file():
        protocol = mode_file.read_text(encoding='utf-8').strip()
        if protocol not in _PROTOCOLS:
            raise SyncError(
                f"Invalid protocol '{protocol}' in {mode_file}. "
                'Fix the file or pass --protocol.'
            )

    if not protocol:
        protocol = 'knowledge'

    mode_file.write_text(f'{protocol}\n', encoding='utf-8')
    return protocol


def _insert_gate_markers(repo_root: Path) -> int:
    rules_dir = repo_root / '.agent-smith' / 'lore' / 'rules_md'
    inserted = 0
    md_files: list[Path] = (
        sorted(p for p in rules_dir.rglob('*.md') if p.is_file())
        if rules_dir.is_dir()
        else []
    )

    for filepath in md_files:
        if 'AGENT GATE:' in filepath.read_text(encoding='utf-8'):
            continue
        with filepath.open('a', encoding='utf-8') as fh:
            fh.write(f'\n{GATE_MARKER}\n')
        print(f'  [marker] {filepath.relative_to(repo_root).as_posix()}')
        inserted += 1

    if not md_files:
        print('WARNING: no rule files found in .agent-smith/lore/rules_md/.')
        print('         Author your rule markdown files there and re-run sync.')

    return inserted


def _points_into_agent_smith(link: Path) -> bool:
    return '.agent-smith/opencode/' in os.readlink(link)


def _place_symlink(
    repo_root: Path,
    rel: str,
    target: str,
    counters: dict[str, int],
    placed: set[Path],
) -> None:
    link = repo_root / '.opencode' / rel
    link.parent.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        if _points_into_agent_smith(link):
            link.unlink()
            link.symlink_to(target)
            counters['refreshed'] += 1
            placed.add(link)
        else:
            print(f'  [skip] .opencode/{rel} is a foreign symlink — left untouched')
            counters['skipped'] += 1
        return

    if link.exists():
        print(f'  [skip] .opencode/{rel} exists and is host-owned — left untouched')
        counters['skipped'] += 1
        return

    link.symlink_to(target)
    counters['created'] += 1
    placed.add(link)


def _prune_stale_links(
    repo_root: Path, placed: set[Path], counters: dict[str, int]
) -> None:
    """Remove agent-smith-owned symlinks the current run did not (re)create.

    Upgrades that change the link layout (e.g. flattening agents/subagents/*
    into agents/*) would otherwise leave the old nested links orphaned. This
    prunes those and any directories they emptied, mirroring _desync cleanup.
    """
    opencode_dir = repo_root / '.opencode'
    if not opencode_dir.is_dir():
        return

    for link in sorted(p for p in opencode_dir.rglob('*') if p.is_symlink()):
        if link in placed:
            continue
        if _points_into_agent_smith(link):
            rel = link.relative_to(opencode_dir).as_posix()
            link.unlink()
            print(f'  [prune] .opencode/{rel} — stale agent-smith link removed')
            counters['pruned'] += 1

    # Prune directories left empty by pruning (deepest first).
    for directory in sorted(
        (p for p in opencode_dir.rglob('*') if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()


def _symlink_opencode(repo_root: Path, protocol: str) -> dict[str, int]:
    src = repo_root / '.agent-smith' / 'opencode'
    if not src.is_dir():
        raise SyncError(f'Opencode scaffolding not found: {src}. Run install.sh first.')

    counters = {'created': 0, 'refreshed': 0, 'skipped': 0, 'pruned': 0}
    placed: set[Path] = set()

    for path in sorted(p for p in src.rglob('*') if p.is_file()):
        rel = path.relative_to(src).as_posix()

        if rel.startswith('AGENTS.instructions.') and rel.endswith('.md'):
            continue

        # opencode discovers agents in a flat .opencode/agents/ directory, so
        # any nested source layout (e.g. agents/subagents/foo.md) is flattened
        # to agents/<basename>. Other trees keep their structure.
        if rel.startswith('agents/'):
            link_rel = f'agents/{path.name}'
        else:
            link_rel = rel

        # Relative target: climb from .opencode/<link-dir> back to repo root.
        depth = link_rel.count('/') + 1
        target = '../' * depth + f'.agent-smith/opencode/{rel}'
        _place_symlink(repo_root, link_rel, target, counters, placed)

    _place_symlink(
        repo_root,
        'AGENTS.instructions.md',
        f'../.agent-smith/opencode/AGENTS.instructions.{protocol}.md',
        counters,
        placed,
    )

    _prune_stale_links(repo_root, placed, counters)
    return counters


def _strip_jsonc(text: str) -> str:
    """Remove JSONC comments and trailing commas without altering strings."""
    without_comments: list[str] = []
    in_string = False
    escaped = False
    index = 0

    while index < len(text):
        char = text[index]

        if in_string:
            without_comments.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            without_comments.append(char)
            index += 1
            continue

        if char == '/' and index + 1 < len(text):
            next_char = text[index + 1]
            if next_char == '/':
                index += 2
                while index < len(text) and text[index] not in '\r\n':
                    index += 1
                continue
            if next_char == '*':
                comment_start = index
                index += 2
                while index + 1 < len(text) and text[index : index + 2] != '*/':
                    if text[index] in '\r\n':
                        without_comments.append(text[index])
                    index += 1
                if index + 1 >= len(text):
                    raise ValueError(
                        f'Unterminated block comment at character {comment_start}'
                    )
                index += 2
                continue

        without_comments.append(char)
        index += 1

    normalized = ''.join(without_comments)
    without_trailing_commas: list[str] = []
    in_string = False
    escaped = False
    index = 0

    while index < len(normalized):
        char = normalized[index]

        if in_string:
            without_trailing_commas.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            without_trailing_commas.append(char)
            index += 1
            continue

        if char == ',':
            lookahead = index + 1
            while lookahead < len(normalized) and normalized[lookahead].isspace():
                lookahead += 1
            if lookahead < len(normalized) and normalized[lookahead] in '}]':
                index += 1
                continue

        without_trailing_commas.append(char)
        index += 1

    return ''.join(without_trailing_commas)


def _load_opencode_config(config_file: Path) -> dict:
    if not config_file.is_file():
        return {}
    try:
        config = json.loads(_strip_jsonc(config_file.read_text(encoding='utf-8')))
    except (json.JSONDecodeError, ValueError) as exc:
        raise SyncError(f'Invalid JSON in {config_file}: {exc}') from exc
    if not isinstance(config, dict):
        raise SyncError(f'Invalid JSON in {config_file}: top-level value must be an object')
    return config


def _write_opencode_config(config_file: Path, config: dict) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix='opencode.json.tmp.', dir=config_file.parent
    )
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(config, fh, indent=2)
        fh.write('\n')
    os.replace(tmp_path, config_file)


def _write_runtime_config(repo_root: Path, protocol: str, version: str) -> None:
    runtime_file = repo_root / '.agent-smith' / 'knowledge_base' / RUNTIME_FILE
    _write_opencode_config(
        runtime_file,
        {
            'version': version,
            'package': _package_source(version),
            'protocol': protocol,
        },
    )


def _write_mcp_config(repo_root: Path, protocol: str, version: str) -> None:
    config_file = repo_root / '.opencode' / 'opencode.json'
    config = _load_opencode_config(config_file)

    config.setdefault('mcp', {})['agent_smith'] = {
        'type': 'local',
        'command': [
            'uvx',
            '--from',
            _package_source(version),
            'agent-smith-mcp',
        ],
        'environment': {
            'AGENT_SMITH_HOME': str(repo_root / '.agent-smith'),
            'AGENT_SMITH_PROJECT_ROOT': str(repo_root),
            'AGENT_SMITH_PROTOCOL': protocol,
        },
        'enabled': True,
    }

    instructions = config.setdefault('instructions', [])
    if not isinstance(instructions, list):
        raise SyncError(
            f'Invalid JSON in {config_file}: instructions must be an array'
        )
    if INSTRUCTIONS_PATH not in instructions:
        instructions.append(INSTRUCTIONS_PATH)

    _write_opencode_config(config_file, config)


def _tui_panel_path(repo_root: Path) -> str:
    return str(repo_root / '.opencode' / 'plugins' / TUI_PANEL_FILE)


def _configure_tui_panel(repo_root: Path, enabled: bool) -> str:
    config_file = repo_root / '.opencode' / 'tui.json'
    if not enabled and not config_file.is_file():
        return 'absent'

    config = _load_opencode_config(config_file)
    plugins = config.get('plugin')
    if plugins is None:
        plugins = []
    elif not isinstance(plugins, list):
        raise SyncError(f'Invalid JSON in {config_file}: plugin must be an array')

    panel_path = _tui_panel_path(repo_root)
    if enabled:
        if panel_path not in plugins:
            plugins.append(panel_path)
        config['plugin'] = plugins
        status = 'registered'
    else:
        remaining = [entry for entry in plugins if entry != panel_path]
        if remaining:
            config['plugin'] = remaining
        else:
            config.pop('plugin', None)
        status = 'removed'

    _write_opencode_config(config_file, config)
    return status


def _pty_plugin_spec(entry) -> str | None:
    """Return the package spec of a `plugin` array entry, or None if the entry
    is not an opencode-pty spec.

    OpenCode formally supports both plain-string plugin entries
    (`"opencode-pty"` / `"opencode-pty@<version>"`) and tuple-form entries
    (`["opencode-pty", {"option": value}]` /
    `["opencode-pty@<version>", {"option": value}]`). The package spec is the
    first element of a tuple; the trailing options object is host-owned and
    must never be rewritten by sync. Any other shape (a non-string spec, a
    non-tuple entry, etc.) is not an opencode-pty spec and returns None so it
    is left untouched.
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, list) and entry and isinstance(entry[0], str):
        return entry[0]
    return None


def _is_unpinned_pty_entry(entry) -> bool:
    """True if `entry` is the unpinned `opencode-pty` registration, in either
    string or tuple form. Used to remove exactly the unpinned registration on
    desync while preserving host-pinned overrides and unrelated plugins."""
    return _pty_plugin_spec(entry) == PTY_PLUGIN_SPEC


def _pty_plugin_state(plugins: list) -> tuple[bool, bool]:
    """Classify existing `plugin` array entries for opencode-pty.

    Returns a (unpinned_present, pinned_present) tuple so both variants can be
    detected independently. A host may have BOTH the unpinned `opencode-pty`
    spec and a host-pinned `opencode-pty@<version>` override in the array at
    the same time; classifying only the first match would miss the other.

    Both plain-string and tuple-form entries are classified equivalently: a
    tuple whose first element is `opencode-pty` is an unpinned host
    registration, and a tuple whose first element is `opencode-pty@<version>`
    is a host-pinned override. Recognising tuple forms prevents sync from
    appending a duplicate plain-string entry next to a host's tuple
    registration and from rewriting the host-owned tuple.
    """
    unpinned = False
    pinned = False
    for entry in plugins:
        spec = _pty_plugin_spec(entry)
        if spec is None:
            continue
        if spec == PTY_PLUGIN_SPEC:
            unpinned = True
        elif spec.startswith(f'{PTY_PLUGIN_SPEC}@'):
            pinned = True
    return unpinned, pinned


def _configure_pty_plugin(repo_root: Path, enabled: bool) -> str:
    """Register/deregister the unpinned `opencode-pty` plugin spec in the
    `plugin` array of .opencode/opencode.json.

    Idempotent in both directions. Unrelated host plugins are always
    preserved. A host-pinned `opencode-pty@<version>` override is never
    touched, never duplicated, and never removed by this function.
    """
    config_file = repo_root / '.opencode' / 'opencode.json'
    if not enabled and not config_file.is_file():
        return 'absent'

    config = _load_opencode_config(config_file)
    plugins = config.get('plugin')
    if plugins is None:
        plugins = []
    elif not isinstance(plugins, list):
        raise SyncError(f'Invalid JSON in {config_file}: plugin must be an array')

    unpinned, pinned = _pty_plugin_state(plugins)

    if enabled:
        if unpinned:
            status = 'already registered'
        elif pinned:
            # Host pinned its own override; do not add a duplicate unpinned
            # entry alongside it.
            status = 'preserved (host-pinned override)'
        else:
            plugins.append(PTY_PLUGIN_SPEC)
            config['plugin'] = plugins
            status = 'registered'
    else:
        if unpinned:
            # Remove exactly the unpinned `opencode-pty` registration (string
            # or tuple form), but preserve any host-pinned
            # `opencode-pty@<version>` override and all unrelated plugins.
            # Sync never rewrites a tuple into a plain string; it removes the
            # whole entry on desync.
            remaining = [
                entry for entry in plugins if not _is_unpinned_pty_entry(entry)
            ]
            if remaining:
                config['plugin'] = remaining
            else:
                config.pop('plugin', None)
            status = 'removed'
        elif pinned:
            status = 'preserved (host-pinned override)'
        else:
            status = 'absent'

    _write_opencode_config(config_file, config)
    return status


def _pty_permission_notice(config: dict) -> str:
    """Report host bash permission policies opencode-pty cannot honour the
    same way the built-in bash tool does, without ever modifying them.

    opencode-pty checks pty_spawn commands against permission.bash, but
    (per the plugin's own documented limitation) treats any "ask" pattern as
    "deny" since a plugin cannot trigger OpenCode's interactive permission
    prompt. Sync never rewrites the host's permission policy to compensate —
    it only surfaces the gap so the host can decide whether to tighten
    "ask" patterns to explicit "allow"/"deny" for PTY-spawned commands.

    A valid top-level `permission` string shorthand is handled without
    crashing, and malformed/unrecognized `permission` or `permission.bash`
    shapes are flagged rather than reported as compatible. The host
    configuration is never modified.
    """
    permission = config.get('permission')
    if permission is None:
        return 'no host-level permission.bash policy configured'

    # Valid top-level shorthand: a bare string applies to all permission
    # categories. There is no bash-specific policy to inspect, so report that
    # rather than crashing or claiming a bash policy exists.
    if isinstance(permission, str):
        return (
            f"top-level permission shorthand '{permission}' configured — "
            'no host-level permission.bash policy to inspect'
        )

    if not isinstance(permission, dict):
        return (
            f"unrecognized permission shape ({type(permission).__name__}) — "
            'host permission config left unchanged; verify it is valid'
        )

    bash_permission = permission.get('bash')

    if bash_permission is None:
        return 'no host-level permission.bash policy configured'

    ask_patterns: list[str] = []
    if bash_permission == 'ask':
        ask_patterns = ['*']
    elif bash_permission in ('allow', 'deny'):
        # Recognized bash shorthand; no ask patterns to flag.
        pass
    elif isinstance(bash_permission, dict):
        ask_patterns = sorted(
            pattern for pattern, mode in bash_permission.items() if mode == 'ask'
        )
    else:
        return (
            f"unrecognized permission.bash shape "
            f"({type(bash_permission).__name__}) — host permission config "
            'left unchanged; verify it is valid'
        )

    if not ask_patterns:
        return 'compatible with configured permission.bash policy'

    return (
        f"{len(ask_patterns)} 'ask' permission.bash pattern(s) "
        f"({', '.join(ask_patterns)}) will be treated as 'deny' for "
        "pty_spawn commands — plugins cannot trigger OpenCode's permission "
        'prompt. Host policy left unchanged; tighten to explicit allow/deny '
        'if PTY access to those commands is required.'
    )


def _pty_external_directory_notice(config: dict) -> str:
    """Report the host's `permission.external_directory` policy and the
    opencode-pty limitation that 'ask' is treated as 'allow' for it.

    Unlike `permission.bash` (where opencode-pty treats 'ask' as 'deny'),
    opencode-pty cannot prompt interactively for external_directory access,
    so an 'ask' mode is coerced to 'allow' — PTY-spawned commands may then
    read or write outside the project directory. Sync only surfaces this gap
    and never rewrites or broadens the host's policy. Malformed/unrecognized
    shapes are flagged rather than reported as compatible.
    """
    permission = config.get('permission')
    if permission is None:
        return 'no host-level permission.external_directory policy configured'
    if isinstance(permission, str):
        return (
            f"top-level permission shorthand '{permission}' configured — "
            'no host-level permission.external_directory policy to inspect'
        )
    if not isinstance(permission, dict):
        return (
            f"unrecognized permission shape ({type(permission).__name__}) — "
            'host permission config left unchanged; verify it is valid'
        )

    external = permission.get('external_directory')
    if external is None:
        return 'no host-level permission.external_directory policy configured'

    ask_patterns: list[str] = []
    if external == 'ask':
        ask_patterns = ['*']
    elif external in ('allow', 'deny'):
        # Recognized shorthand; no ask coercion to flag.
        pass
    elif isinstance(external, dict):
        ask_patterns = sorted(
            pattern for pattern, mode in external.items() if mode == 'ask'
        )
    else:
        return (
            f"unrecognized permission.external_directory shape "
            f"({type(external).__name__}) — host permission config left "
            'unchanged; verify it is valid'
        )

    if not ask_patterns:
        return 'compatible with configured permission.external_directory policy'

    return (
        f"{len(ask_patterns)} 'ask' permission.external_directory pattern(s) "
        f"({', '.join(ask_patterns)}) will be treated as 'allow' by "
        "opencode-pty (no interactive prompt) — PTY-spawned commands may "
        'read/write outside the project directory. Host policy left '
        'unchanged; tighten to explicit allow/deny if that access is not '
        'intended.'
    )


def _sync_kb(repo_root: Path, protocol: str) -> str:
    if protocol != 'knowledge':
        return 'skipped (rules protocol)'

    scenarios_dir = repo_root / '.agent-smith' / 'lore' / 'json' / 'scenarios'
    has_scenarios = scenarios_dir.is_dir() and any(scenarios_dir.rglob('*.json'))

    if not has_scenarios:
        print('WARNING: protocol is knowledge but no scenario JSON found in')
        print('         .agent-smith/lore/json/scenarios/. The knowledge base will be')
        print('         unavailable and agent_smith_init will fall back to the rules protocol.')
        return 'skipped (no scenarios)'

    os.environ['AGENT_SMITH_HOME'] = str(repo_root / '.agent-smith')
    os.environ['AGENT_SMITH_PROJECT_ROOT'] = str(repo_root)
    os.environ['AGENT_SMITH_PROTOCOL'] = protocol

    import agent_smith.config as config_module

    config_module._config = None

    from agent_smith.kdb.sync_kb import sync as kb_sync
    from agent_smith.mcp.kb.query import reset as reset_kb_query

    kb_sync()
    reset_kb_query()
    return 'synced'


def _activate(repo_root: Path, protocol_flag: str | None) -> None:
    version = _tool_version()
    _warn_on_version_drift(version)
    package = _package_source(version)
    protocol = _resolve_protocol(repo_root, protocol_flag)
    markers_inserted = _insert_gate_markers(repo_root)
    counters = _symlink_opencode(repo_root, protocol)
    _write_runtime_config(repo_root, protocol, version)
    _write_mcp_config(repo_root, protocol, version)
    tui_status = _configure_tui_panel(repo_root, protocol == 'knowledge')
    pty_status = _configure_pty_plugin(repo_root, True)
    pty_permission_status = _pty_permission_notice(
        _load_opencode_config(repo_root / '.opencode' / 'opencode.json')
    )
    pty_external_dir_status = _pty_external_directory_notice(
        _load_opencode_config(repo_root / '.opencode' / 'opencode.json')
    )
    kb_status = _sync_kb(repo_root, protocol)

    if shutil.which('uv') is None:
        print()
        print("WARNING: 'uv' not found on PATH. The MCP server is configured to run")
        print('         via uvx and will not start without it:')
        print('         curl -LsSf https://astral.sh/uv/install.sh | sh')

    print(
        f"""
Sync summary
------------
Tool version: {version}
Protocol: {protocol} (persisted in .agent-smith/.mode)
Gate markers inserted: {markers_inserted}
Symlinks created: {counters['created']}, refreshed: {counters['refreshed']}, skipped: {counters['skipped']}, pruned: {counters['pruned']}
MCP config status: written
TUI panel status: {tui_status}
PTY plugin status: {pty_status}
PTY permission compatibility: {pty_permission_status}
PTY external_directory compatibility: {pty_external_dir_status}
KB sync status: {kb_status}
NOTE: Restart OpenCode after plugin/agent configuration changes for pty_*
      tools and updated agent definitions to take effect.
NOTE: opencode-pty requires OpenCode >={MIN_OPENCODE_VERSION}. It keeps only
      a rolling in-memory output buffer per PTY session (default 50,000
      lines) — read output with bounded pty_read calls (offset/limit/pattern)
      rather than unbounded dumps.
Deactivate command: uvx --from "{package}" agent-smith-sync --repo-root "{repo_root}" --desync"""
    )


def _desync(repo_root: Path) -> None:
    opencode_dir = repo_root / '.opencode'
    removed = 0

    if opencode_dir.is_dir():
        for link in sorted(p for p in opencode_dir.rglob('*') if p.is_symlink()):
            if _points_into_agent_smith(link):
                link.unlink()
                print(f'  [unlink] {link.relative_to(repo_root).as_posix()}')
                removed += 1

        # Prune directories left empty by link removal (deepest first).
        for directory in sorted(
            (p for p in opencode_dir.rglob('*') if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()

    config_file = opencode_dir / 'opencode.json'
    if config_file.is_file():
        config = _load_opencode_config(config_file)
        config.get('mcp', {}).pop('agent_smith', None)
        instructions = config.get('instructions')
        if instructions is not None:
            if not isinstance(instructions, list):
                raise SyncError(
                    f'Invalid JSON in {config_file}: instructions must be an array'
                )
            config['instructions'] = [
                entry for entry in instructions if entry != INSTRUCTIONS_PATH
            ]
            if not config['instructions']:
                config.pop('instructions')
        _write_opencode_config(config_file, config)
        mcp_status = 'stripped'
    else:
        mcp_status = 'absent'

    tui_status = _configure_tui_panel(repo_root, False)
    pty_status = _configure_pty_plugin(repo_root, False)

    version = _tool_version()
    package = _package_source(version)
    print(
        f"""
Desync summary
--------------
Symlinks removed: {removed}
MCP config status: {mcp_status}
TUI panel status: {tui_status}
PTY plugin status: {pty_status}
.agent-smith/ left intact (gate markers preserved).
Reactivate command: uvx --from "{package}" agent-smith-sync --repo-root "{repo_root}\""""
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='agent-smith-sync',
        description='Activate (default) or deactivate (--desync) Agent Smith in a host project.',
    )
    parser.add_argument(
        '--repo-root',
        help='Project root. Default: walk up from the current directory to the '
        'nearest .git. Non-git projects must pass this explicitly.',
    )
    parser.add_argument(
        '--protocol',
        choices=_PROTOCOLS,
        help='rules or knowledge. Default: value stored in .agent-smith/.mode, '
        'or knowledge if never synced.',
    )
    parser.add_argument(
        '--desync',
        action='store_true',
        help='Deactivate instead of activate.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        repo_root = _resolve_repo_root(args.repo_root)

        if not (repo_root / '.agent-smith').is_dir():
            raise SyncError(
                f'.agent-smith not found at {repo_root}. Run install.sh first.'
            )

        if args.desync:
            _desync(repo_root)
        else:
            _activate(repo_root, args.protocol)
    except SyncError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
