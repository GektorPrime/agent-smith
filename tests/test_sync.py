from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_smith.scripts import sync

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def host(tmp_path: Path) -> Path:
    """Host repo with mirrored wireframe (as left by install.sh)."""
    host = tmp_path / 'host'
    host.mkdir()
    subprocess.run(['git', 'init', '-q', '.'], cwd=host, check=True)
    shutil.copytree(REPO_ROOT / 'wireframe', host / '.agent-smith')
    return host


def _run(host: Path, *args: str) -> int:
    return sync.main(['--repo-root', str(host), *args])


def test_sync_default_knowledge_activation(host: Path, capsys) -> None:
    assert _run(host) == 0
    out = capsys.readouterr().out

    assert 'WARNING: no rule files found' in out
    assert 'no scenario JSON found' in out
    assert 'KB sync status: skipped (no scenarios)' in out

    assert (host / '.agent-smith' / '.mode').read_text().strip() == 'knowledge'

    instructions = host / '.opencode' / 'AGENTS.instructions.md'
    assert instructions.is_symlink()
    assert 'AGENTS.instructions.knowledge.md' in str(instructions.readlink())
    # Protocol-variant sources are not linked directly
    assert not (host / '.opencode' / 'AGENTS.instructions.rules.md').exists()

    # Agents are flattened into .opencode/agents/, regardless of nesting in
    # the source tree, because opencode only discovers agents at that level.
    flat = host / '.opencode' / 'agents' / 'basher.md'
    assert flat.is_symlink()
    assert flat.is_file()  # link resolves
    assert flat.readlink().as_posix() == (
        '../../.agent-smith/opencode/agents/subagents/basher.md'
    )
    assert not (host / '.opencode' / 'agents' / 'subagents').exists()

    config = json.loads((host / '.opencode' / 'opencode.json').read_text())
    block = config['mcp']['agent_smith']
    assert config['instructions'] == [sync.INSTRUCTIONS_PATH]
    version = sync._tool_version()
    assert block['command'][1] == '--from'
    assert block['command'][2].endswith(f'@{version}')
    assert block['command'][3] == 'agent-smith-mcp'
    assert block['environment']['AGENT_SMITH_PROTOCOL'] == 'knowledge'
    assert block['environment']['AGENT_SMITH_HOME'] == str(host / '.agent-smith')
    runtime = json.loads(
        (host / '.agent-smith' / 'knowledge_base' / sync.RUNTIME_FILE).read_text()
    )
    assert runtime == {
        'version': version,
        'package': f'git+{sync.GIT_URL}@{version}',
        'protocol': 'knowledge',
    }
    tui = json.loads((host / '.opencode' / 'tui.json').read_text())
    assert tui['plugin'] == [sync._tui_panel_path(host)]


def test_sync_runtime_prefers_installed_release_path(
    host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir = host / 'installed release'
    install_dir.mkdir()
    monkeypatch.setenv('AGENT_SMITH_INSTALL_DIR', str(install_dir))

    assert _run(host) == 0

    runtime = json.loads(
        (host / '.agent-smith' / 'knowledge_base' / sync.RUNTIME_FILE).read_text()
    )
    assert runtime['package'] == str(install_dir)
    opencode = json.loads((host / '.opencode' / 'opencode.json').read_text())
    assert opencode['mcp']['agent_smith']['command'] == [
        'uvx',
        '--from',
        str(install_dir),
        'agent-smith-mcp',
    ]


def test_sync_inserts_markers_and_is_idempotent(host: Path, capsys) -> None:
    rule = host / '.agent-smith' / 'lore' / 'rules_md' / 'my-rule.md'
    rule.write_text('# My Rule\n\nContent.\n', encoding='utf-8')

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'Gate markers inserted: 1' in out
    content = rule.read_text(encoding='utf-8')
    assert content.count('AGENT GATE:') == 1
    assert 'PLACEHOLDER-PLACEHOLDER-PLACEHOLDER' in content

    # Second run: no duplicate markers, links refreshed not duplicated
    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'Gate markers inserted: 0' in out
    assert rule.read_text(encoding='utf-8').count('AGENT GATE:') == 1


def test_sync_inserts_markers_in_nested_rule_files(host: Path, capsys) -> None:
    nested = host / '.agent-smith' / 'lore' / 'rules_md' / 'batch' / 'sub'
    nested.mkdir(parents=True)
    rule = nested / 'deep-rule.md'
    rule.write_text('# Deep Rule\n', encoding='utf-8')

    assert _run(host) == 0
    out = capsys.readouterr().out

    assert 'Gate markers inserted: 1' in out
    assert '[marker] .agent-smith/lore/rules_md/batch/sub/deep-rule.md' in out
    assert 'AGENT GATE:' in rule.read_text(encoding='utf-8')


def test_sync_marks_symlinked_rule_files_through_link(
    host: Path, tmp_path: Path, capsys
) -> None:
    external = tmp_path / 'external.md'
    external.write_text('# External\n', encoding='utf-8')
    batch = host / '.agent-smith' / 'lore' / 'rules_md' / 'linked'
    batch.mkdir(parents=True)
    (batch / 'external.md').symlink_to(external)

    assert _run(host) == 0
    out = capsys.readouterr().out

    assert 'Gate markers inserted: 1' in out
    # Marker written through the symlink into the external source
    assert 'AGENT GATE:' in external.read_text(encoding='utf-8')


def test_sync_warns_when_newer_release_installed(
    host: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    fake_home = tmp_path / 'home'
    tool_base = fake_home / '.agent-smith-tool'
    tool_base.mkdir(parents=True)
    # A much newer release is installed than whatever version is running.
    (tool_base / '999.0.0').mkdir()
    (tool_base / 'not-a-version').mkdir()  # ignored
    monkeypatch.setenv('HOME', str(fake_home))

    assert _run(host) == 0
    out = capsys.readouterr().out

    assert 'WARNING: running agent-smith' in out
    assert '999.0.0 is installed' in out
    assert 'uv tool install --force' in out


def test_sync_no_drift_warning_when_running_is_current(
    host: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    fake_home = tmp_path / 'home'
    tool_base = fake_home / '.agent-smith-tool'
    tool_base.mkdir(parents=True)
    # Older release installed than the running version -> no warning.
    (tool_base / '0.0.1').mkdir()
    monkeypatch.setenv('HOME', str(fake_home))

    assert _run(host) == 0
    out = capsys.readouterr().out

    assert 'WARNING: running agent-smith' not in out


def test_sync_prunes_stale_agent_smith_links(host: Path, capsys) -> None:
    """Links from an older layout that the current run no longer produces are
    pruned, and directories they emptied are removed."""
    # Simulate a pre-flatten sync: a nested subagents/ link pointing into
    # .agent-smith, plus a foreign link and a host file that must survive.
    subagents = host / '.opencode' / 'agents' / 'subagents'
    subagents.mkdir(parents=True)
    stale = subagents / 'basher.md'
    stale.symlink_to('../../../.agent-smith/opencode/agents/subagents/basher.md')
    foreign = host / '.opencode' / 'agents' / 'subagents' / 'foreign.md'
    foreign.symlink_to('/etc/hosts')
    host_owned = host / '.opencode' / 'agents' / 'host-note.md'
    host_owned.write_text('mine\n', encoding='utf-8')

    assert _run(host) == 0
    out = capsys.readouterr().out

    # Stale nested link pruned; flat replacement created.
    assert '[prune] .opencode/agents/subagents/basher.md' in out
    assert not stale.exists()
    assert (host / '.opencode' / 'agents' / 'basher.md').is_file()

    # Foreign link keeps its dir alive; it and the host file are untouched.
    assert foreign.is_symlink()
    assert subagents.is_dir()
    assert host_owned.read_text() == 'mine\n'


def test_sync_never_clobbers_host_owned_files(host: Path, capsys) -> None:
    commands_dir = host / '.opencode' / 'commands'
    commands_dir.mkdir(parents=True)
    host_file = commands_dir / 'agent-smith-init.md'
    host_file.write_text('host-owned\n', encoding='utf-8')

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'host-owned — left untouched' in out
    assert host_file.read_text() == 'host-owned\n'
    assert not host_file.is_symlink()


def test_sync_skips_foreign_symlink_on_activation(
    host: Path, tmp_path: Path, capsys
) -> None:
    """A pre-existing symlink NOT pointing into .agent-smith is left untouched."""
    external = tmp_path / 'external-init.md'
    external.write_text('external target\n', encoding='utf-8')
    commands_dir = host / '.opencode' / 'commands'
    commands_dir.mkdir(parents=True)
    foreign = commands_dir / 'agent-smith-init.md'
    foreign.symlink_to(external)

    assert _run(host) == 0
    out = capsys.readouterr().out

    assert 'foreign symlink — left untouched' in out
    # Untouched: still points at the external target, not into .agent-smith.
    assert foreign.is_symlink()
    assert '.agent-smith/opencode/' not in foreign.readlink().as_posix()
    assert foreign.readlink() == external


def test_sync_refreshes_stale_agent_smith_symlink(host: Path) -> None:
    """A symlink already pointing into .agent-smith/opencode/ is relinked."""
    commands_dir = host / '.opencode' / 'commands'
    commands_dir.mkdir(parents=True)
    link = commands_dir / 'agent-smith-init.md'
    # Stale target: points into .agent-smith/opencode/ but at a wrong path.
    link.symlink_to('../../.agent-smith/opencode/commands/stale-old-name.md')
    assert '.agent-smith/opencode/' in link.readlink().as_posix()

    assert _run(host) == 0

    # Refreshed to the correct current target and resolves to a real file.
    assert link.is_symlink()
    assert link.readlink().as_posix().endswith(
        '.agent-smith/opencode/commands/agent-smith-init.md'
    )
    assert link.is_file()


def test_sync_preserves_host_instructions_and_is_idempotent(host: Path) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'instructions': ['AGENTS.md', 'docs/conventions.md']}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    assert _run(host) == 0

    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['instructions'] == [
        'AGENTS.md',
        'docs/conventions.md',
        sync.INSTRUCTIONS_PATH,
    ]


def test_sync_accepts_jsonc_comments_and_trailing_commas(host: Path) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '''{
  // Host instructions remain active.
  "instructions": ["AGENTS.md",],
  "url": "https://example.com/a//b",
  "label": "literal /* text */ and ,}",
  "mcp": {
    /* Existing integrations are preserved. */
    "host": {"enabled": true,},
  },
}
''',
        encoding='utf-8',
    )

    assert _run(host) == 0

    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['instructions'] == ['AGENTS.md', sync.INSTRUCTIONS_PATH]
    assert config['url'] == 'https://example.com/a//b'
    assert config['label'] == 'literal /* text */ and ,}'
    assert config['mcp']['host'] == {'enabled': True}


def test_sync_rejects_non_array_instructions(host: Path, capsys) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"instructions": "AGENTS.md"}', encoding='utf-8')

    assert _run(host) == 1
    assert 'instructions must be an array' in capsys.readouterr().err


def test_sync_preserves_tui_jsonc_and_plugin_registration(host: Path) -> None:
    config_path = host / '.opencode' / 'tui.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '''{
  // Host TUI configuration.
  "theme": "custom",
  "plugin": ["/host/plugin.ts",],
}
''',
        encoding='utf-8',
    )

    assert _run(host) == 0
    assert _run(host) == 0

    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['theme'] == 'custom'
    assert config['plugin'] == ['/host/plugin.ts', sync._tui_panel_path(host)]


def test_sync_rejects_non_array_tui_plugins(host: Path, capsys) -> None:
    config_path = host / '.opencode' / 'tui.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"plugin": "/host/plugin.ts"}', encoding='utf-8')

    assert _run(host) == 1
    assert 'plugin must be an array' in capsys.readouterr().err


def test_sync_registers_unpinned_pty_plugin(host: Path, capsys) -> None:
    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: registered' in out

    config = json.loads((host / '.opencode' / 'opencode.json').read_text())
    assert config['plugin'] == [sync.PTY_PLUGIN_SPEC]


def test_sync_pty_plugin_registration_is_idempotent(host: Path, capsys) -> None:
    assert _run(host) == 0
    capsys.readouterr()
    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: already registered' in out

    config = json.loads((host / '.opencode' / 'opencode.json').read_text())
    assert config['plugin'] == [sync.PTY_PLUGIN_SPEC]


def test_sync_preserves_unrelated_host_plugins_when_registering_pty(host: Path) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'plugin': ['some-other-plugin']}), encoding='utf-8'
    )

    assert _run(host) == 0

    config = json.loads(config_path.read_text())
    assert config['plugin'] == ['some-other-plugin', sync.PTY_PLUGIN_SPEC]


def test_sync_preserves_host_pinned_pty_plugin_override(host: Path, capsys) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'plugin': ['opencode-pty@1.2.3', 'other-plugin']}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: preserved (host-pinned override)' in out

    config = json.loads(config_path.read_text())
    # Unchanged: no duplicate unpinned entry added alongside the host's pin.
    assert config['plugin'] == ['opencode-pty@1.2.3', 'other-plugin']


def test_sync_recognizes_tuple_form_unpinned_pty_plugin_as_registered(
    host: Path, capsys
) -> None:
    """A tuple-form `['opencode-pty', options]` registration must be recognised
    as the unpinned host registration: activation must not append a duplicate
    plain-string entry, and the host's tuple (with its options) must be
    preserved exactly."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'plugin': [['opencode-pty', {'enabled': True}], 'other-plugin']}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: already registered' in out

    config = json.loads(config_path.read_text())
    # No duplicate plain-string entry appended; tuple preserved verbatim.
    assert config['plugin'] == [['opencode-pty', {'enabled': True}], 'other-plugin']


def test_sync_recognizes_tuple_form_pinned_pty_plugin_override(
    host: Path, capsys
) -> None:
    """A tuple-form `['opencode-pty@<version>', options]` registration must be
    classified as a host-pinned override: activation must not append a
    duplicate unpinned entry, and the host's tuple must be preserved."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'plugin': [['opencode-pty@1.2.3', {'enabled': True}]]}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: preserved (host-pinned override)' in out

    config = json.loads(config_path.read_text())
    assert config['plugin'] == [['opencode-pty@1.2.3', {'enabled': True}]]


def test_sync_activation_composes_plugin_registration_and_executor_pty_access(
    host: Path, capsys
) -> None:
    """One activation must produce BOTH the unpinned opencode-pty plugin
    registration AND flattened agent symlinks whose execution-capable agent
    content grants pty_* access. The two halves are asserted independently so
    the test fails if either is absent."""
    assert _run(host) == 0
    capsys.readouterr()

    # Half 1: the unpinned opencode-pty plugin spec is registered.
    config = json.loads((host / '.opencode' / 'opencode.json').read_text())
    plugins = config.get('plugin', [])
    assert sync.PTY_PLUGIN_SPEC in plugins, (
        'activation did not register the unpinned opencode-pty plugin spec'
    )

    # Half 2: the execution-capable executor agent is flattened into
    # .opencode/agents/ and its content grants pty_* access.
    executor_link = host / '.opencode' / 'agents' / 'executor.md'
    assert executor_link.is_symlink(), (
        'executor agent was not flattened into .opencode/agents/'
    )
    assert executor_link.is_file(), 'executor agent symlink does not resolve'
    content = executor_link.read_text(encoding='utf-8')
    assert '"pty_*": allow' in content, (
        'flattened executor agent content does not grant pty_* access'
    )


def test_sync_reports_pty_permission_compatible_by_default(host: Path, capsys) -> None:
    assert _run(host) == 0
    out = capsys.readouterr().out
    assert (
        'PTY permission compatibility: no host-level permission.bash policy '
        'configured' in out
    )


def test_sync_reports_pty_permission_ask_pattern_without_modifying_it(
    host: Path, capsys
) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'permission': {'bash': {'npm *': 'allow', 'git push': 'ask'}}}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert (
        "PTY permission compatibility: 1 'ask' permission.bash pattern(s) "
        '(git push) will be treated as ' in out
    )
    assert 'Host policy left unchanged' in out

    # Never silently broadened or rewritten.
    config = json.loads(config_path.read_text())
    assert config['permission'] == {'bash': {'npm *': 'allow', 'git push': 'ask'}}


def test_sync_reports_multiple_pty_permission_ask_patterns(host: Path, capsys) -> None:
    """Multiple `ask` patterns in `permission.bash` must all be reported
    (counted and listed) without modifying the host policy."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'permission': {'bash': {
            'git push': 'ask',
            'rm -rf *': 'ask',
            'npm *': 'allow',
        }}}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert "2 'ask' permission.bash pattern(s) (git push, rm -rf *)" in out
    assert 'Host policy left unchanged' in out

    # Host policy preserved exactly, including the non-ask entries.
    config = json.loads(config_path.read_text())
    assert config['permission'] == {'bash': {
        'git push': 'ask',
        'rm -rf *': 'ask',
        'npm *': 'allow',
    }}


def test_sync_reports_pty_permission_bash_mapping_without_ask_as_compatible(
    host: Path, capsys
) -> None:
    """A `permission.bash` mapping containing no `ask` entries must be reported
    as compatible, and the host policy must be preserved exactly."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'permission': {'bash': {
            'npm *': 'allow',
            'rm -rf *': 'deny',
        }}}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert (
        'PTY permission compatibility: compatible with configured '
        'permission.bash policy' in out
    )

    # Host policy preserved exactly.
    config = json.loads(config_path.read_text())
    assert config['permission'] == {'bash': {
        'npm *': 'allow',
        'rm -rf *': 'deny',
    }}


def test_sync_reports_pty_permission_top_level_ask_string(host: Path, capsys) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'permission': {'bash': 'ask'}}), encoding='utf-8'
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert "1 'ask' permission.bash pattern(s) (*)" in out

    config = json.loads(config_path.read_text())
    assert config['permission'] == {'bash': 'ask'}


def test_sync_reports_pty_permission_top_level_string_shorthand(
    host: Path, capsys
) -> None:
    """A valid top-level `permission` string shorthand must not crash sync."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({'permission': 'ask'}), encoding='utf-8')

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert "top-level permission shorthand 'ask' configured" in out
    assert 'PTY permission compatibility:' in out

    # Host config preserved exactly.
    config = json.loads(config_path.read_text())
    assert config['permission'] == 'ask'


def test_sync_reports_pty_permission_malformed_shapes_as_unrecognized(
    host: Path, capsys
) -> None:
    """Malformed/unrecognized `permission` or `permission.bash` shapes must be
    flagged, not reported as compatible."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)

    # Top-level permission is neither a dict nor a recognized string.
    config_path.write_text(json.dumps({'permission': 123}), encoding='utf-8')
    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'unrecognized permission shape' in out
    assert 'compatible' not in out
    # Host permission policy preserved exactly (sync only adds mcp/plugin).
    assert json.loads(config_path.read_text())['permission'] == 123

    # permission.bash is a list (unrecognized shape).
    config_path.write_text(
        json.dumps({'permission': {'bash': ['git *']}}), encoding='utf-8'
    )
    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'unrecognized permission.bash shape' in out
    assert 'compatible' not in out
    assert json.loads(config_path.read_text())['permission'] == {
        'bash': ['git *']
    }


def test_sync_reports_pty_external_directory_ask_treated_as_allow(
    host: Path, capsys
) -> None:
    """opencode-pty treats `permission.external_directory` 'ask' as 'allow';
    sync must report this gap without modifying the host policy."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'permission': {'external_directory': 'ask'}}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY external_directory compatibility:' in out
    assert "treated as 'allow' by opencode-pty" in out

    # Host policy preserved exactly.
    config = json.loads(config_path.read_text())
    assert config['permission'] == {'external_directory': 'ask'}


def test_sync_reports_pty_external_directory_compatible_by_default(
    host: Path, capsys
) -> None:
    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY external_directory compatibility:' in out
    assert (
        'no host-level permission.external_directory policy configured' in out
    )


def test_sync_reports_pty_external_directory_shorthand_allow(
    host: Path, capsys
) -> None:
    """The `permission.external_directory` 'allow' shorthand must be reported
    as compatible without modifying the host policy."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'permission': {'external_directory': 'allow'}}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY external_directory compatibility:' in out
    assert (
        'compatible with configured permission.external_directory policy' in out
    )

    config = json.loads(config_path.read_text())
    assert config['permission'] == {'external_directory': 'allow'}


def test_sync_reports_pty_external_directory_shorthand_deny(
    host: Path, capsys
) -> None:
    """The `permission.external_directory` 'deny' shorthand must be reported
    as compatible without modifying the host policy."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'permission': {'external_directory': 'deny'}}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'PTY external_directory compatibility:' in out
    assert (
        'compatible with configured permission.external_directory policy' in out
    )

    config = json.loads(config_path.read_text())
    assert config['permission'] == {'external_directory': 'deny'}


def test_sync_reminds_to_restart_opencode_and_documents_min_version(
    host: Path, capsys
) -> None:
    assert _run(host) == 0
    out = capsys.readouterr().out
    assert 'Restart OpenCode after plugin/agent configuration changes' in out
    assert f'opencode-pty requires OpenCode >={sync.MIN_OPENCODE_VERSION}' in out


def test_sync_kb_resets_cached_query_state(host: Path, monkeypatch) -> None:
    scenarios = host / '.agent-smith' / 'lore' / 'json' / 'scenarios'
    (scenarios / 'scenario.json').write_text('[]\n', encoding='utf-8')
    calls: list[str] = []

    monkeypatch.setattr('agent_smith.kdb.sync_kb.sync', lambda: calls.append('sync'))
    monkeypatch.setattr(
        'agent_smith.mcp.kb.query.reset', lambda: calls.append('reset')
    )

    assert sync._sync_kb(host, 'knowledge') == 'synced'
    assert calls == ['sync', 'reset']


def test_sync_protocol_switch_and_mode_persistence(host: Path, capsys) -> None:
    assert _run(host, '--protocol', 'rules') == 0
    assert (host / '.agent-smith' / '.mode').read_text().strip() == 'rules'
    instructions = host / '.opencode' / 'AGENTS.instructions.md'
    assert 'AGENTS.instructions.rules.md' in str(instructions.readlink())
    config = json.loads((host / '.opencode' / 'opencode.json').read_text())
    assert config['mcp']['agent_smith']['environment']['AGENT_SMITH_PROTOCOL'] == 'rules'
    runtime_path = host / '.agent-smith' / 'knowledge_base' / sync.RUNTIME_FILE
    assert json.loads(runtime_path.read_text())['protocol'] == 'rules'
    tui_path = host / '.opencode' / 'tui.json'
    assert not tui_path.exists()

    # Re-sync without flag keeps persisted protocol
    assert _run(host) == 0
    assert (host / '.agent-smith' / '.mode').read_text().strip() == 'rules'

    # Switch back repoints the instructions link
    assert _run(host, '--protocol', 'knowledge') == 0
    assert 'AGENTS.instructions.knowledge.md' in str(instructions.readlink())
    assert json.loads(runtime_path.read_text())['protocol'] == 'knowledge'
    assert json.loads(tui_path.read_text())['plugin'] == [
        sync._tui_panel_path(host)
    ]

    # Switching back removes only the knowledge-mode panel registration.
    assert _run(host, '--protocol', 'rules') == 0
    assert 'plugin' not in json.loads(tui_path.read_text())


def test_sync_rejects_invalid_protocol(host: Path) -> None:
    with pytest.raises(SystemExit):
        sync.main(['--repo-root', str(host), '--protocol', 'bogus'])


def test_sync_rejects_corrupt_mode_file(host: Path, capsys) -> None:
    (host / '.agent-smith' / '.mode').write_text('bogus\n', encoding='utf-8')
    assert _run(host) == 1
    assert 'Invalid protocol' in capsys.readouterr().err


def test_desync_removes_links_strips_config_keeps_markers(host: Path, capsys) -> None:
    rule = host / '.agent-smith' / 'lore' / 'rules_md' / 'my-rule.md'
    rule.write_text('# My Rule\n', encoding='utf-8')

    assert _run(host) == 0

    # Host-owned additions that must survive desync
    config_path = host / '.opencode' / 'opencode.json'
    config = json.loads(config_path.read_text())
    config['theme'] = 'custom'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    foreign_link = host / '.opencode' / 'foreign-link'
    foreign_link.symlink_to('/etc/hosts')
    tui_path = host / '.opencode' / 'tui.json'
    tui = json.loads(tui_path.read_text())
    tui['theme'] = 'host-theme'
    tui['plugin'].insert(0, '/host/plugin.ts')
    tui_path.write_text(json.dumps(tui), encoding='utf-8')

    assert _run(host, '--desync') == 0

    remaining_links = [p for p in (host / '.opencode').rglob('*') if p.is_symlink()]
    assert remaining_links == [foreign_link]

    config = json.loads(config_path.read_text())
    assert 'agent_smith' not in config.get('mcp', {})
    assert config['theme'] == 'custom'
    assert 'instructions' not in config
    tui = json.loads(tui_path.read_text())
    assert tui == {'theme': 'host-theme', 'plugin': ['/host/plugin.ts']}

    # Q14a: markers and .mode preserved
    assert 'AGENT GATE:' in rule.read_text(encoding='utf-8')
    assert (host / '.agent-smith' / '.mode').exists()


def test_desync_preserves_host_instructions(host: Path) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'instructions': ['AGENTS.md', sync.INSTRUCTIONS_PATH]}),
        encoding='utf-8',
    )

    assert _run(host) == 0
    assert _run(host, '--desync') == 0

    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['instructions'] == ['AGENTS.md']


def test_desync_removes_unpinned_pty_plugin_preserving_unrelated_plugins(
    host: Path, capsys
) -> None:
    assert _run(host) == 0
    config_path = host / '.opencode' / 'opencode.json'
    config = json.loads(config_path.read_text())
    assert config['plugin'] == [sync.PTY_PLUGIN_SPEC]
    config['plugin'].insert(0, 'unrelated-plugin')
    config_path.write_text(json.dumps(config), encoding='utf-8')

    capsys.readouterr()
    assert _run(host, '--desync') == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: removed' in out

    config = json.loads(config_path.read_text())
    assert config['plugin'] == ['unrelated-plugin']


def test_desync_pty_plugin_removal_is_idempotent(host: Path, capsys) -> None:
    assert _run(host) == 0
    assert _run(host, '--desync') == 0
    capsys.readouterr()
    assert _run(host, '--desync') == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: absent' in out


def test_desync_preserves_host_pinned_pty_plugin_override(host: Path, capsys) -> None:
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'plugin': ['opencode-pty@1.2.3']}), encoding='utf-8'
    )

    assert _run(host, '--desync') == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: preserved (host-pinned override)' in out

    config = json.loads(config_path.read_text())
    assert config['plugin'] == ['opencode-pty@1.2.3']


def test_desync_removes_unpinned_pty_plugin_even_when_pinned_present(
    host: Path, capsys
) -> None:
    """Desync must remove the exact unpinned `opencode-pty` entry even when a
    host-pinned `opencode-pty@<version>` entry also exists, preserving the
    pinned override and any unrelated plugins regardless of array ordering."""
    for ordering in (
        ['opencode-pty@1.2.3', 'opencode-pty', 'unrelated-plugin'],
        ['opencode-pty', 'opencode-pty@1.2.3', 'unrelated-plugin'],
    ):
        config_path = host / '.opencode' / 'opencode.json'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({'plugin': ordering}), encoding='utf-8'
        )

        capsys.readouterr()
        assert _run(host, '--desync') == 0
        out = capsys.readouterr().out
        assert 'PTY plugin status: removed' in out

        config = json.loads(config_path.read_text())
        # Unpinned entry gone; pinned override and unrelated plugin preserved.
        assert config['plugin'] == ['opencode-pty@1.2.3', 'unrelated-plugin']


def test_desync_removes_tuple_form_unpinned_pty_plugin(
    host: Path, capsys
) -> None:
    """Desync must remove a tuple-form unpinned `opencode-pty` registration
    (treating it equivalently to the plain-string unpinned spec) while
    preserving any host-pinned override and unrelated plugins. Sync never
    rewrites the host's tuple into a plain string; it removes the whole
    entry."""
    config_path = host / '.opencode' / 'opencode.json'
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({'plugin': [
            ['opencode-pty', {'enabled': True}],
            'opencode-pty@1.2.3',
            'unrelated-plugin',
        ]}),
        encoding='utf-8',
    )

    capsys.readouterr()
    assert _run(host, '--desync') == 0
    out = capsys.readouterr().out
    assert 'PTY plugin status: removed' in out

    config = json.loads(config_path.read_text())
    # Tuple-form unpinned entry gone; pinned override and unrelated preserved.
    assert config['plugin'] == ['opencode-pty@1.2.3', 'unrelated-plugin']


def test_sync_requires_agent_smith_dir(host: Path, capsys) -> None:
    shutil.rmtree(host / '.agent-smith')
    assert _run(host) == 1
    assert '.agent-smith not found' in capsys.readouterr().err


def test_sync_non_git_host_with_explicit_root(tmp_path: Path, capsys) -> None:
    host = tmp_path / 'nongit'
    host.mkdir()
    shutil.copytree(REPO_ROOT / 'wireframe', host / '.agent-smith')

    assert sync.main(['--repo-root', str(host), '--protocol', 'rules']) == 0
    out = capsys.readouterr().out
    assert 'Protocol: rules' in out
    assert (host / '.opencode' / 'AGENTS.instructions.md').is_symlink()


def test_sync_auto_discovery_outside_git_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert sync.main([]) == 1
    err = capsys.readouterr().err
    assert 'Not inside a git repository' in err
    assert '--repo-root' in err
