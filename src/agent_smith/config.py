from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def configure_cli_environment(start: Path | None = None) -> None:
    """Populate Agent Smith environment variables for standalone CLI use."""
    if all(
        os.getenv(name)
        for name in (
            'AGENT_SMITH_HOME',
            'AGENT_SMITH_PROJECT_ROOT',
            'AGENT_SMITH_PROTOCOL',
        )
    ):
        return

    current = (start or Path.cwd()).resolve()
    for project_root in (current, *current.parents):
        home = project_root / '.agent-smith'
        if not home.is_dir():
            continue

        os.environ.setdefault('AGENT_SMITH_HOME', str(home))
        os.environ.setdefault('AGENT_SMITH_PROJECT_ROOT', str(project_root))
        if not os.getenv('AGENT_SMITH_PROTOCOL'):
            mode_file = home / '.mode'
            protocol = (
                mode_file.read_text(encoding='utf-8').strip()
                if mode_file.is_file()
                else 'knowledge'
            )
            os.environ['AGENT_SMITH_PROTOCOL'] = protocol
        return

    raise ConfigError(
        'Could not find .agent-smith in the current directory or its parents. '
        'Run this command from an activated host repository.'
    )


def _runtime_package() -> str:
    configure_cli_environment()
    runtime_path = (
        Path(os.environ['AGENT_SMITH_HOME']) / 'knowledge_base' / 'runtime.json'
    )
    try:
        runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
        package = runtime['package']
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigError(
            f'Invalid Agent Smith runtime metadata at {runtime_path}'
        ) from exc
    if not isinstance(package, str) or not package:
        raise ConfigError('Agent Smith runtime package must be a non-empty string')
    return package


def reexec_cli_runtime(
    command: str,
    *,
    extra: str | None = None,
    module: str | None = None,
) -> None:
    """Re-execute a CLI through the host's runtime package."""
    bootstrap_id = f'{command}:{extra or "core"}'
    if os.getenv('AGENT_SMITH_CLI_BOOTSTRAPPED') == bootstrap_id:
        suffix = f' with the {extra} extra' if extra else ''
        raise ConfigError(
            f'The configured Agent Smith runtime{suffix} cannot run {command}.'
        )

    package = _runtime_package()
    environment = os.environ.copy()
    environment['AGENT_SMITH_CLI_BOOTSTRAPPED'] = bootstrap_id
    if extra:
        uvx = shutil.which('uvx')
        if uvx is None:
            raise ConfigError(f'Cannot bootstrap {command}: uvx is not available on PATH')
        target = ['python', '-m', module] if module else [command]
        os.execve(
            uvx,
            [
                uvx,
                '--isolated',
                '--from',
                package,
                '--with',
                f'agent-smith[{extra}] @ {package}',
                *target,
                *sys.argv[1:],
            ],
            environment,
        )

    uvx = shutil.which('uvx')
    if uvx is None:
        raise ConfigError(f'Cannot bootstrap {command}: uvx is not available on PATH')
    os.execve(
        uvx,
        [uvx, '--isolated', '--from', package, command, *sys.argv[1:]],
        environment,
    )


def ensure_sqlite_cli_runtime(command: str) -> None:
    """Use the host runtime when this Python cannot load SQLite extensions."""
    conn = sqlite3.connect(':memory:')
    try:
        if hasattr(conn, 'enable_load_extension'):
            return
    finally:
        conn.close()

    reexec_cli_runtime(command)


class Config:
    def __init__(self) -> None:
        self._agent_smith_home = self._require_env(
            'AGENT_SMITH_HOME',
            'Set AGENT_SMITH_HOME to the absolute path of the host repo .agent-smith directory.',
        )
        self._agent_smith_project_root = self._require_env(
            'AGENT_SMITH_PROJECT_ROOT',
            'Set AGENT_SMITH_PROJECT_ROOT to the absolute path of the host repository root.',
        )
        self._agent_smith_protocol = self._require_env(
            'AGENT_SMITH_PROTOCOL',
            'Set AGENT_SMITH_PROTOCOL to one of: rules, knowledge.',
        )
        self._agent_smith_install_dir = os.getenv('AGENT_SMITH_INSTALL_DIR')

        self._validate()

    @staticmethod
    def _require_env(name: str, message: str) -> str:
        value = os.getenv(name)
        if not value:
            raise ConfigError(f'{name}: {message}')
        return value

    def _validate(self) -> None:
        home = Path(self._agent_smith_home)
        if not home.is_dir():
            raise ConfigError(
                'AGENT_SMITH_HOME: Path does not exist as a directory. '
                'Set AGENT_SMITH_HOME to an existing absolute directory path.'
            )

        project_root = Path(self._agent_smith_project_root)
        if not project_root.is_dir():
            raise ConfigError(
                'AGENT_SMITH_PROJECT_ROOT: Path does not exist as a directory. '
                'Set AGENT_SMITH_PROJECT_ROOT to an existing absolute directory path.'
            )

        if self._agent_smith_protocol not in {'rules', 'knowledge'}:
            raise ConfigError(
                'AGENT_SMITH_PROTOCOL: Invalid value. '
                'Use AGENT_SMITH_PROTOCOL=rules or AGENT_SMITH_PROTOCOL=knowledge.'
            )

        if self._agent_smith_install_dir is not None:
            install_dir = Path(self._agent_smith_install_dir)
            if not install_dir.is_absolute():
                raise ConfigError(
                    'AGENT_SMITH_INSTALL_DIR: Must be an absolute path when set. '
                    'Set AGENT_SMITH_INSTALL_DIR to an absolute extracted release directory path.'
                )

    def home(self) -> Path:
        return Path(self._agent_smith_home)

    def project_root(self) -> Path:
        return Path(self._agent_smith_project_root)

    def protocol(self) -> str:
        return self._agent_smith_protocol

    def install_dir(self) -> Path | None:
        if self._agent_smith_install_dir is None:
            return None
        return Path(self._agent_smith_install_dir)

    def rules_db_path(self) -> Path:
        return self.home() / 'knowledge_base' / 'rules.db'

    def scenarios_path(self) -> Path:
        return self.home() / 'lore' / 'json' / 'scenarios'

    def exemplars_path(self) -> Path:
        return self.home() / 'lore' / 'json' / 'code_exemplars'

    def rules_md_path(self) -> Path:
        return self.home() / 'lore' / 'rules_md'

    def rules_core_path(self) -> Path:
        return self.rules_md_path() / 'core'

    def routing_table_path(self) -> Path:
        return self.home() / 'lore' / 'rules_routing_table.md'

    def entry_point_path(self) -> Path:
        return self.home() / 'entry-point.md'


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
