from __future__ import annotations

import os
from pathlib import Path

import pytest

import agent_smith.config as config_module
from agent_smith.config import (
    Config,
    ConfigError,
    configure_cli_environment,
    ensure_sqlite_cli_runtime,
    get_config,
    reexec_cli_runtime,
)


def test_config_raises_when_home_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('AGENT_SMITH_HOME', raising=False)
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', '/tmp')
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    with pytest.raises(ConfigError):
        Config()


def test_config_raises_when_project_root_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_path))
    monkeypatch.delenv('AGENT_SMITH_PROJECT_ROOT', raising=False)
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    with pytest.raises(ConfigError):
        Config()


def test_config_raises_when_protocol_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.delenv('AGENT_SMITH_PROTOCOL', raising=False)
    with pytest.raises(ConfigError):
        Config()


def test_config_raises_when_protocol_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'invalid')
    with pytest.raises(ConfigError):
        Config()


def test_config_raises_when_home_path_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    non_existent = tmp_path / 'missing-home'
    monkeypatch.setenv('AGENT_SMITH_HOME', str(non_existent))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    with pytest.raises(ConfigError):
        Config()


def test_valid_config_paths_and_helpers(agent_smith_env: dict[str, Path | str]) -> None:
    cfg = Config()
    home = Path(agent_smith_env['home'])
    project_root = Path(agent_smith_env['project_root'])

    assert cfg.home() == home
    assert cfg.project_root() == project_root
    assert cfg.protocol() == 'knowledge'
    assert cfg.entry_point_path() == home / 'entry-point.md'
    assert cfg.rules_db_path().as_posix().endswith('knowledge_base/rules.db')
    assert cfg.scenarios_path().as_posix().endswith('lore/json/scenarios')


def test_get_config_is_singleton(agent_smith_env: dict[str, Path | str]) -> None:
    config_module._config = None
    first = get_config()
    second = get_config()
    assert first is second


def test_configure_cli_environment_discovers_host_from_nested_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'host'
    nested = project_root / 'src' / 'package'
    home = project_root / '.agent-smith'
    nested.mkdir(parents=True)
    home.mkdir()
    (home / '.mode').write_text('rules\n', encoding='utf-8')
    for name in (
        'AGENT_SMITH_HOME',
        'AGENT_SMITH_PROJECT_ROOT',
        'AGENT_SMITH_PROTOCOL',
    ):
        monkeypatch.delenv(name, raising=False)

    configure_cli_environment(nested)

    assert Path(os.environ['AGENT_SMITH_HOME']) == home
    assert Path(os.environ['AGENT_SMITH_PROJECT_ROOT']) == project_root
    assert os.environ['AGENT_SMITH_PROTOCOL'] == 'rules'


def test_configure_cli_environment_errors_outside_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        'AGENT_SMITH_HOME',
        'AGENT_SMITH_PROJECT_ROOT',
        'AGENT_SMITH_PROTOCOL',
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError, match='Could not find .agent-smith'):
        configure_cli_environment(tmp_path)


def test_ensure_sqlite_cli_runtime_reexecs_through_runtime_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / '.agent-smith'
    knowledge_base = home / 'knowledge_base'
    knowledge_base.mkdir(parents=True)
    (knowledge_base / 'runtime.json').write_text(
        '{"package": "/opt/agent smith/1.0.4"}\n', encoding='utf-8'
    )
    monkeypatch.setenv('AGENT_SMITH_HOME', str(home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    monkeypatch.setattr(
        config_module.shutil,
        'which',
        lambda command: f'/opt/bin/{command}',
    )
    monkeypatch.setattr('sys.argv', ['agent-smith-kb-sync', '--dry-run'])

    class _Connection:
        def close(self) -> None:
            pass

    monkeypatch.setattr(config_module.sqlite3, 'connect', lambda _: _Connection())
    captured: dict = {}

    def fake_execve(path: str, args: list[str], env: dict[str, str]) -> None:
        captured.update(path=path, args=args, env=env)
        raise RuntimeError('exec intercepted')

    monkeypatch.setattr(config_module.os, 'execve', fake_execve)

    with pytest.raises(RuntimeError, match='exec intercepted'):
        ensure_sqlite_cli_runtime('agent-smith-kb-sync')

    assert captured['path'] == '/opt/bin/uvx'
    assert captured['args'] == [
        '/opt/bin/uvx',
        '--isolated',
        '--from',
        '/opt/agent smith/1.0.4',
        'agent-smith-kb-sync',
        '--dry-run',
    ]
    assert captured['env']['AGENT_SMITH_CLI_BOOTSTRAPPED'] == (
        'agent-smith-kb-sync:core'
    )


def test_reexec_cli_runtime_adds_extra_to_local_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / '.agent-smith'
    knowledge_base = home / 'knowledge_base'
    knowledge_base.mkdir(parents=True)
    (knowledge_base / 'runtime.json').write_text(
        '{"package": "/opt/agent-smith/1.0.5"}\n', encoding='utf-8'
    )
    monkeypatch.setenv('AGENT_SMITH_HOME', str(home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    monkeypatch.setattr(
        config_module.shutil,
        'which',
        lambda command: f'/opt/bin/{command}',
    )
    monkeypatch.setattr('sys.argv', ['agent-smith-kb-visualize', '--no-spotlight'])
    captured: dict = {}

    def fake_execve(path: str, args: list[str], env: dict[str, str]) -> None:
        captured.update(path=path, args=args, env=env)
        raise RuntimeError('exec intercepted')

    monkeypatch.setattr(config_module.os, 'execve', fake_execve)

    with pytest.raises(RuntimeError, match='exec intercepted'):
        reexec_cli_runtime(
            'agent-smith-kb-visualize',
            extra='viz',
            module='agent_smith.kdb.tools.visualize_kb',
        )

    assert captured['args'] == [
        '/opt/bin/uvx',
        '--isolated',
        '--from',
        '/opt/agent-smith/1.0.5',
        '--with',
        'agent-smith[viz] @ /opt/agent-smith/1.0.5',
        'python',
        '-m',
        'agent_smith.kdb.tools.visualize_kb',
        '--no-spotlight',
    ]
    assert captured['env']['AGENT_SMITH_CLI_BOOTSTRAPPED'] == (
        'agent-smith-kb-visualize:viz'
    )
