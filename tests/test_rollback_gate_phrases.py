from __future__ import annotations

import re
import subprocess
from pathlib import Path

from agent_smith.scripts.rollback_gate_phrases import main


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ['git', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git_repo(project_root: Path) -> None:
    _git(['init', '-q', '.'], project_root)
    _git(['add', '-A'], project_root)
    _git(
        ['-c', 'user.email=test@test', '-c', 'user.name=test', 'commit', '-qm', 'init'],
        project_root,
    )


def _rotate_token(filepath: Path, new_token: str) -> None:
    content = filepath.read_text(encoding='utf-8')
    filepath.write_text(
        re.sub(r'(AGENT GATE: Include the phrase ")[^"]+(")', rf'\g<1>{new_token}\g<2>', content),
        encoding='utf-8',
    )


def test_gate_only_diff_is_reverted(gated_home: Path, capsys) -> None:
    project_root = gated_home.parent
    _init_git_repo(project_root)

    entry_point = gated_home / 'entry-point.md'
    original = entry_point.read_text(encoding='utf-8')
    _rotate_token(entry_point, 'ZZZZ-YYYY-XXXX')
    assert entry_point.read_text(encoding='utf-8') != original

    exit_code = main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert 'Reverted entry-point.md: gate-phrase-only change.' in out
    assert entry_point.read_text(encoding='utf-8') == original


def test_mixed_diff_is_skipped(gated_home: Path, capsys) -> None:
    project_root = gated_home.parent
    _init_git_repo(project_root)

    rule = gated_home / 'lore' / 'rules_md' / 'marked-rule.md'
    _rotate_token(rule, 'ZZZZ-YYYY-XXXX')
    content = rule.read_text(encoding='utf-8')
    rule.write_text(content.replace('# Marked Rule', '# Marked Rule (edited)'), encoding='utf-8')

    exit_code = main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert 'Skipped lore/rules_md/marked-rule.md: non-gate diff detected.' in out
    # Substantive edit survives
    assert '# Marked Rule (edited)' in rule.read_text(encoding='utf-8')
    assert 'ZZZZ-YYYY-XXXX' in rule.read_text(encoding='utf-8')


def test_no_diff_is_noop(gated_home: Path, capsys) -> None:
    project_root = gated_home.parent
    _init_git_repo(project_root)

    exit_code = main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert 'Reverted' not in out.replace('Reverted 0 file(s)', '')
    assert 'Reverted 0 file(s), skipped 0 file(s).' in out


def test_mixed_repo_state_partial_revert(gated_home: Path, capsys) -> None:
    project_root = gated_home.parent
    _init_git_repo(project_root)

    entry_point = gated_home / 'entry-point.md'
    persona = gated_home / 'persona.md'
    original_entry = entry_point.read_text(encoding='utf-8')

    _rotate_token(entry_point, 'ZZZZ-YYYY-XXXX')
    persona.write_text(
        persona.read_text(encoding='utf-8').replace('# Persona', '# Persona (edited)'),
        encoding='utf-8',
    )

    exit_code = main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert 'Reverted entry-point.md: gate-phrase-only change.' in out
    assert 'Skipped persona.md: non-gate diff detected.' in out
    assert 'Reverted 1 file(s), skipped 1 file(s).' in out
    assert entry_point.read_text(encoding='utf-8') == original_entry
    assert '# Persona (edited)' in persona.read_text(encoding='utf-8')
