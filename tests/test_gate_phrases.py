from __future__ import annotations

import re
from pathlib import Path

from agent_smith.mcp.gates.regenerate import (
    GATE_PATTERN,
    _discover_gate_files,
    generate_token,
    regenerate,
)

TOKEN_RE = re.compile(r'^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$')


def _current_token(filepath: Path) -> str:
    match = re.search(r'AGENT GATE: Include the phrase "([^"]+)"', filepath.read_text(encoding='utf-8'))
    assert match, f'no gate marker in {filepath}'
    return match.group(1)


def test_generate_token_format() -> None:
    for _ in range(20):
        assert TOKEN_RE.match(generate_token())


def test_discover_gate_files(gated_home: Path) -> None:
    discovered = {p.relative_to(gated_home).as_posix() for p in _discover_gate_files()}
    assert discovered == {
        'entry-point.md',
        'persona.md',
        'lore/rules_md/core/core-rule.md',
        'lore/rules_md/marked-rule.md',
        'lore/rules_md/unmarked-rule.md',
    }


def test_regenerate_rotates_tokens_in_marked_files(gated_home: Path) -> None:
    report = regenerate()

    assert 'UPDATED entry-point.md: gate phrase regenerated.' in report
    assert 'UPDATED persona.md: gate phrase regenerated.' in report
    assert 'UPDATED lore/rules_md/marked-rule.md: gate phrase regenerated.' in report
    assert 'UPDATED lore/rules_md/core/core-rule.md: gate phrase regenerated.' in report
    assert 'Regenerated 4 gate phrase(s).' in report

    entry_token = _current_token(gated_home / 'entry-point.md')
    persona_token = _current_token(gated_home / 'persona.md')
    rule_token = _current_token(gated_home / 'lore' / 'rules_md' / 'marked-rule.md')

    assert TOKEN_RE.match(entry_token)
    assert TOKEN_RE.match(persona_token)
    assert TOKEN_RE.match(rule_token)
    assert entry_token != 'PLACEHOLDER-PLACEHOLDER-PLACEHOLDER'

    # Per-document tokens: each marked file gets its own unique token
    assert len({entry_token, persona_token, rule_token}) == 3

    # Marker structure intact
    assert GATE_PATTERN.search((gated_home / 'entry-point.md').read_text(encoding='utf-8'))


def test_regenerate_warns_on_files_without_marker(gated_home: Path) -> None:
    report = regenerate()
    assert 'WARNING: No AGENT GATE comment found in lore/rules_md/unmarked-rule.md, skipping.' in report
    assert 'UPDATED lore/rules_md/unmarked-rule.md' not in report
    # Unmarked file content untouched
    assert (gated_home / 'lore' / 'rules_md' / 'unmarked-rule.md').read_text(
        encoding='utf-8'
    ) == '# Unmarked Rule\n'


def test_regenerate_twice_produces_different_tokens(gated_home: Path) -> None:
    regenerate()
    first = _current_token(gated_home / 'entry-point.md')
    regenerate()
    second = _current_token(gated_home / 'entry-point.md')
    assert first != second
    assert TOKEN_RE.match(first) and TOKEN_RE.match(second)


def test_discover_gate_files_recurses_subfolders(gated_home: Path) -> None:
    nested = gated_home / 'lore' / 'rules_md' / 'batch' / 'sub'
    nested.mkdir(parents=True)
    (nested / 'deep-rule.md').write_text('# Deep\n', encoding='utf-8')

    discovered = {p.relative_to(gated_home).as_posix() for p in _discover_gate_files()}
    assert 'lore/rules_md/batch/sub/deep-rule.md' in discovered


def test_regenerate_writes_through_symlinked_rule(
    gated_home: Path, tmp_path: Path
) -> None:
    external = tmp_path / 'external.md'
    external.write_text(
        '# External\n\n'
        '<!-- AGENT GATE: Include the phrase "PLACEHOLDER-PLACEHOLDER-PLACEHOLDER" '
        'in your reading acknowledgment to confirm this file was read to completion. -->\n',
        encoding='utf-8',
    )
    batch = gated_home / 'lore' / 'rules_md' / 'linked'
    batch.mkdir(parents=True)
    (batch / 'external.md').symlink_to(external)

    report = regenerate()

    assert 'UPDATED lore/rules_md/linked/external.md: gate phrase regenerated.' in report
    # Token was rotated through the symlink into the external source
    token = _current_token(external)
    assert TOKEN_RE.match(token)
    assert token != 'PLACEHOLDER-PLACEHOLDER-PLACEHOLDER'


def test_regenerate_reports_no_updates_when_nothing_matches(
    monkeypatch, tmp_path: Path
) -> None:
    import agent_smith.config as config_module

    home = tmp_path / '.agent-smith'
    (home / 'lore' / 'rules_md').mkdir(parents=True)
    (home / 'entry-point.md').write_text('# No marker here\n', encoding='utf-8')

    monkeypatch.setenv('AGENT_SMITH_HOME', str(home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'rules')
    config_module._config = None

    report = regenerate()
    assert 'No files were updated.' in report
