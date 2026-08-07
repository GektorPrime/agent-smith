from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_smith.scripts import install

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_checkout(root: Path, version: str, content: str) -> Path:
    checkout = root / f'checkout-{version}'
    plugin = checkout / 'wireframe' / 'opencode' / 'plugins' / 'plugin.ts'
    plugin.parent.mkdir(parents=True)
    plugin.write_text(content, encoding='utf-8')
    subprocess.run(['git', 'init', '-q'], cwd=checkout, check=True)
    subprocess.run(['git', 'add', '.'], cwd=checkout, check=True)
    subprocess.run(
        [
            'git',
            '-c',
            'user.name=Agent Smith Tests',
            '-c',
            'user.email=agent-smith@example.test',
            'commit',
            '-qm',
            version,
        ],
        cwd=checkout,
        check=True,
    )
    subprocess.run(['git', 'tag', version], cwd=checkout, check=True)
    return checkout


def _run_install(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    host: Path,
    checkout: Path,
    version: str,
) -> None:
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('AGENT_SMITH_SOURCE_DIR', str(checkout))
    monkeypatch.setattr(
        'sys.argv',
        ['agent-smith-install-release', '--repo-root', str(host), '--version', version],
    )
    install.main()


def test_upgrade_refreshes_release_file_but_preserves_customization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / 'home'
    host = tmp_path / 'host'
    home.mkdir()
    host.mkdir()
    old_checkout = _make_checkout(tmp_path, '1.0.0', 'old release\n')
    new_checkout = _make_checkout(tmp_path, '1.1.0', 'new release\n')
    plugin = host / '.agent-smith' / 'opencode' / 'plugins' / 'plugin.ts'

    _run_install(monkeypatch, home, host, old_checkout, '1.0.0')
    assert plugin.read_text(encoding='utf-8') == 'old release\n'

    _run_install(monkeypatch, home, host, new_checkout, '1.1.0')
    assert plugin.read_text(encoding='utf-8') == 'new release\n'

    plugin.write_text('host customization\n', encoding='utf-8')
    newer_checkout = _make_checkout(tmp_path, '1.2.0', 'newer release\n')
    _run_install(monkeypatch, home, host, newer_checkout, '1.2.0')
    assert plugin.read_text(encoding='utf-8') == 'host customization\n'


def test_install_refreshes_persistent_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    home = tmp_path / 'home'
    host = tmp_path / 'host'
    home.mkdir()
    host.mkdir()
    checkout = _make_checkout(tmp_path, '1.1.3', 'release\n')

    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        # Intercept only the persistent-CLI refresh; delegate git to the real
        # subprocess so release acquisition still works.
        if isinstance(cmd, list) and cmd[:2] == ['uv', 'tool']:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(install.shutil, 'which', lambda name: '/usr/bin/uv')
    monkeypatch.setattr(install.subprocess, 'run', fake_run)

    _run_install(monkeypatch, home, host, checkout, '1.1.3')

    install_dir = home / '.agent-smith-tool' / '1.1.3'
    assert ['uv', 'tool', 'install', '--force', str(install_dir)] in calls
    out = capsys.readouterr().out
    assert 'Persistent CLI: refreshed' in out
    # Bare command is now current, so the next step drops the uvx --from prefix.
    assert f'Next step: agent-smith-sync --repo-root "{host}"' in out


def test_install_degrades_when_uv_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    home = tmp_path / 'home'
    host = tmp_path / 'host'
    home.mkdir()
    host.mkdir()
    checkout = _make_checkout(tmp_path, '1.1.3', 'release\n')

    real_run = subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ['uv', 'tool']:
            raise AssertionError('uv tool install must not run when uv is absent')
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(install.shutil, 'which', lambda name: None)
    monkeypatch.setattr(install.subprocess, 'run', guarded_run)

    _run_install(monkeypatch, home, host, checkout, '1.1.3')

    out = capsys.readouterr().out
    assert 'Persistent CLI: skipped (uv not found)' in out
    # Install itself still succeeded; steer to the pinned invocation.
    install_dir = home / '.agent-smith-tool' / '1.1.3'
    assert f'uvx --from "{install_dir}" agent-smith-sync' in out
    assert install_dir.is_dir()


def test_uninstall_does_not_require_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / 'home'
    release = home / '.agent-smith-tool' / '1.0.0'
    release.mkdir(parents=True)
    outside_repo = tmp_path / 'not-a-repo'
    outside_repo.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.chdir(outside_repo)
    monkeypatch.delenv('AGENT_SMITH_SOURCE_DIR', raising=False)
    monkeypatch.setattr(
        'sys.argv', ['agent-smith-install-release', '--version', '1.0.0', '--uninstall']
    )

    install.main()

    assert not release.exists()


def _seed_synced_project(host: Path) -> dict[str, Path]:
    """Create a project that looks activated: .agent-smith source + .opencode
    symlinks/config pointing into it, plus foreign/host-owned artifacts."""
    src = host / '.agent-smith' / 'opencode'
    (src / 'agents' / 'subagents').mkdir(parents=True)
    (src / 'agents' / 'analyst.md').write_text('analyst', encoding='utf-8')
    (src / 'agents' / 'subagents' / 'basher.md').write_text('basher', encoding='utf-8')
    (src / 'AGENTS.instructions.knowledge.md').write_text('kb', encoding='utf-8')

    opencode = host / '.opencode'
    (opencode / 'agents' / 'subagents').mkdir(parents=True)
    (opencode / 'plugins').mkdir(parents=True)

    owned = {
        'agents/analyst.md': '../../.agent-smith/opencode/agents/analyst.md',
        'agents/subagents/basher.md':
            '../../../.agent-smith/opencode/agents/subagents/basher.md',
        'AGENTS.instructions.md':
            '../.agent-smith/opencode/AGENTS.instructions.knowledge.md',
    }
    for rel, target in owned.items():
        (opencode / rel).symlink_to(target)

    # Foreign symlink (not owned) and a host-owned real file must survive.
    foreign = opencode / 'plugins' / 'foreign.ts'
    foreign.symlink_to('../../elsewhere/foreign.ts')
    host_file = opencode / 'host-owned.md'
    host_file.write_text('mine', encoding='utf-8')

    panel_path = str(opencode / 'plugins' / 'agent-smith-rules-panel.tsx')
    (opencode / 'opencode.json').write_text(
        json.dumps(
            {
                'mcp': {'agent_smith': {'command': ['x']}, 'other': {'command': ['y']}},
                'instructions': ['.opencode/AGENTS.instructions.md', 'keep.md'],
            }
        ),
        encoding='utf-8',
    )
    (opencode / 'tui.json').write_text(
        json.dumps({'plugin': [panel_path, '/keep/panel.tsx']}), encoding='utf-8'
    )

    return {
        'opencode': opencode,
        'foreign': foreign,
        'host_file': host_file,
    }


def test_uninstall_can_purge_host_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / 'home'
    release = home / '.agent-smith-tool' / '1.0.0'
    release.mkdir(parents=True)
    host = tmp_path / 'host'
    artifacts = _seed_synced_project(host)
    opencode = artifacts['opencode']

    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('AGENT_SMITH_SOURCE_DIR', raising=False)
    monkeypatch.setattr(
        'sys.argv',
        [
            'agent-smith-install-release', '--version', '1.0.0', '--uninstall',
            '--purge-host-data', '--repo-root', str(host),
        ],
    )

    install.main()

    assert not release.exists()
    assert not (host / '.agent-smith').exists()

    # Owned symlinks removed and now-empty dirs pruned.
    assert not (opencode / 'agents' / 'analyst.md').is_symlink()
    assert not (opencode / 'agents' / 'subagents' / 'basher.md').is_symlink()
    assert not (opencode / 'AGENTS.instructions.md').is_symlink()
    assert not (opencode / 'agents').exists()

    # Foreign symlink and host-owned file untouched.
    assert artifacts['foreign'].is_symlink()
    assert artifacts['host_file'].read_text(encoding='utf-8') == 'mine'

    # Config entries stripped, unrelated entries preserved.
    config = json.loads((opencode / 'opencode.json').read_text(encoding='utf-8'))
    assert 'agent_smith' not in config.get('mcp', {})
    assert 'other' in config['mcp']
    assert config['instructions'] == ['keep.md']

    tui = json.loads((opencode / 'tui.json').read_text(encoding='utf-8'))
    assert tui['plugin'] == ['/keep/panel.tsx']


def test_purge_aborts_when_desync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / 'home'
    release = home / '.agent-smith-tool' / '1.0.0'
    release.mkdir(parents=True)
    host = tmp_path / 'host'
    _seed_synced_project(host)
    # Corrupt opencode.json so _desync raises SyncError before rmtree runs.
    (host / '.opencode' / 'opencode.json').write_text('{ not json', encoding='utf-8')

    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('AGENT_SMITH_SOURCE_DIR', raising=False)
    monkeypatch.setattr(
        'sys.argv',
        [
            'agent-smith-install-release', '--version', '1.0.0', '--uninstall',
            '--purge-host-data', '--repo-root', str(host),
        ],
    )

    with pytest.raises(SystemExit):
        install.main()

    # Aborted before nuking host data; release cache is still removed first.
    assert (host / '.agent-smith').is_dir()


def test_latest_tag_uses_version_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        install,
        '_run_git',
        lambda *args, **kwargs: '\n'.join(
            [
                'a\trefs/tags/1.9.0',
                'b\trefs/tags/1.10.0',
                'c\trefs/tags/1.10.0^{}',
                'd\trefs/tags/latest',
            ]
        ),
    )

    assert install._latest_remote_tag() == '1.10.0'


def test_uninstall_rejects_version_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / 'victim'
    victim.mkdir()
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('AGENT_SMITH_SOURCE_DIR', raising=False)
    monkeypatch.setattr(
        'sys.argv', ['agent-smith-install-release', '--version', str(victim), '--uninstall']
    )

    with pytest.raises(SystemExit):
        install.main()

    assert victim.is_dir()


def test_remote_acquisition_fetches_exact_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def record(*args: str, cwd: Path | None = None) -> str:
        calls.append((args, cwd))
        return ''

    monkeypatch.setattr(install, '_run_git', record)
    destination = tmp_path / 'release'
    destination.mkdir()

    install._acquire_release('1.2.3', destination, None)

    assert any(
        args == ('fetch', '--quiet', '--depth', '1', 'origin', 'refs/tags/1.2.3')
        for args, _ in calls
    )


def test_compatibility_script_delegates_to_console_command(tmp_path: Path) -> None:
    bin_dir = tmp_path / 'bin'
    tool_bin = tmp_path / 'tool-bin'
    bin_dir.mkdir()
    tool_bin.mkdir()
    args_file = tmp_path / 'args'
    uv_calls = tmp_path / 'uv-calls'
    uv = bin_dir / 'uv'
    uv.write_text(
        '#!/usr/bin/env bash\n'
        'printf \'%s\\n\' "$*" >> "$UV_CALLS"\n'
        'if [[ "$1 $2 $3" == "tool dir --bin" ]]; then printf \'%s\\n\' "$TOOL_BIN"; fi\n',
        encoding='utf-8',
    )
    uv.chmod(0o755)
    installed_command = tool_bin / 'agent-smith-install-release'
    installed_command.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$ARGS_FILE"\n',
        encoding='utf-8',
    )
    installed_command.chmod(0o755)
    script = tmp_path / 'install.sh'
    shutil.copy2(REPO_ROOT / 'install.sh', script)

    subprocess.run(
        ['bash', str(script), '--version', '1.2.3', '--uninstall'],
        cwd=tmp_path,
        env={
            'PATH': f'{bin_dir}:{os.environ["PATH"]}',
            'ARGS_FILE': str(args_file),
            'TOOL_BIN': str(tool_bin),
            'UV_CALLS': str(uv_calls),
        },
        check=True,
    )

    assert uv_calls.read_text(encoding='utf-8').splitlines() == [
        'tool install --force git+https://github.com/lightspeedretail/agent_smith.git@mainframe',
        'tool dir --bin',
    ]
    assert args_file.read_text(encoding='utf-8').splitlines() == [
        '--version',
        '1.2.3',
        '--uninstall',
    ]
