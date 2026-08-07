from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

import agent_smith.config as config_module


REPO_ROOT = Path(__file__).resolve().parents[1]
WIREFRAME_ROOT = REPO_ROOT / 'wireframe'


@pytest.fixture
def agent_smith_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path | str]]:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(WIREFRAME_ROOT))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(REPO_ROOT))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None
    yield {
        'home': WIREFRAME_ROOT,
        'project_root': REPO_ROOT,
        'protocol': 'knowledge',
    }
    config_module._config = None


@pytest.fixture
def tmp_agent_smith_home(tmp_path: Path) -> Path:
    home = tmp_path / 'agent-smith-home'
    shutil.copytree(WIREFRAME_ROOT, home)
    return home


GATE_MARKER = (
    '<!-- AGENT GATE: Include the phrase "PLACEHOLDER-PLACEHOLDER-PLACEHOLDER" '
    'in your reading acknowledgment to confirm this file was read to completion. -->'
)


@pytest.fixture
def gated_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """
    Temp .agent-smith home with gate markers: entry-point.md and persona.md
    (both marked), plus two rule files — one marked, one without a marker.
    Environment is pointed at it with the rules protocol.
    """
    home = tmp_path / '.agent-smith'
    rules_dir = home / 'lore' / 'rules_md'
    core_dir = rules_dir / 'core'
    core_dir.mkdir(parents=True)

    (home / 'entry-point.md').write_text(f'# Entry\n\n{GATE_MARKER}\n', encoding='utf-8')
    (home / 'persona.md').write_text(f'# Persona\n\n{GATE_MARKER}\n', encoding='utf-8')
    (home / 'lore' / 'rules_routing_table.md').write_text(
        '# Routing\n\n| Task | Rule files |\n|------|-----------|\n'
        '| API work | marked-rule.md |\n',
        encoding='utf-8',
    )
    (rules_dir / 'marked-rule.md').write_text(
        f'# Marked Rule\n\n{GATE_MARKER}\n', encoding='utf-8'
    )
    (rules_dir / 'unmarked-rule.md').write_text('# Unmarked Rule\n', encoding='utf-8')
    (core_dir / 'core-rule.md').write_text(
        f'# Core Rule\n\n{GATE_MARKER}\n', encoding='utf-8'
    )

    monkeypatch.setenv('AGENT_SMITH_HOME', str(home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'rules')
    config_module._config = None
    yield home
    config_module._config = None
