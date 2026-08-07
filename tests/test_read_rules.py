from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_smith.mcp.gates import readers
from agent_smith.mcp.gates.readers import read_rules, select_rules


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    readers._state.reset()
    yield
    readers._state.reset()


def _drain() -> list[str]:
    """Drive read_rules to completion, returning each served response."""
    responses: list[str] = []
    guard = 0
    while True:
        guard += 1
        assert guard < 100, 'read_rules did not terminate'
        result = read_rules()
        responses.append(result)
        if '=== DONE' in result:
            break
    return responses


def test_read_rules_serves_one_file_per_call(gated_home: Path) -> None:
    select_rules([])  # empty -> all
    first = read_rules()
    # Exactly one file header in a single response.
    assert first.count('=== ') == 2  # file header + NEXT/DONE marker
    assert '=== NEXT' in first or '=== DONE' in first


def test_read_rules_walks_all_files_with_markers(gated_home: Path) -> None:
    select_rules([])  # all files
    responses = _drain()
    joined = '\n'.join(responses)
    assert '=== core/core-rule.md ===' in joined
    assert '=== marked-rule.md ===' in joined
    assert '=== unmarked-rule.md ===' in joined
    # Only the final response carries DONE; all others carry NEXT.
    assert responses[-1].count('=== DONE') == 1
    for r in responses[:-1]:
        assert '=== NEXT' in r


def test_read_rules_records_served_tokens(gated_home: Path) -> None:
    select_rules([])
    _drain()
    # Marked files contribute a token; unmarked-rule.md does not.
    assert 'marked-rule.md' in readers._state.served_tokens
    assert 'core/core-rule.md' in readers._state.served_tokens
    assert 'unmarked-rule.md' not in readers._state.served_tokens


def test_read_rules_auto_pins_all_when_select_skipped(gated_home: Path) -> None:
    # No select_rules call: read_rules must auto-pin all.
    responses = _drain()
    joined = '\n'.join(responses)
    assert readers._state.pinned is True
    assert '=== marked-rule.md ===' in joined
    assert '=== core/core-rule.md ===' in joined


def test_read_rules_selection_limits_served_files(gated_home: Path) -> None:
    select_rules(['marked-rule.md'])
    responses = _drain()
    joined = '\n'.join(responses)
    # Selected file + core (always) served; unselected non-core excluded.
    assert '=== marked-rule.md ===' in joined
    assert '=== core/core-rule.md ===' in joined
    assert '=== unmarked-rule.md ===' not in joined


def test_read_rules_recurses_subfolders_with_relative_headers(gated_home: Path) -> None:
    rules_dir = gated_home / 'lore' / 'rules_md'
    nested = rules_dir / 'batch' / 'sub'
    nested.mkdir(parents=True)
    (nested / 'deep-rule.md').write_text('# Deep Rule\n', encoding='utf-8')

    select_rules([])
    joined = '\n'.join(_drain())
    assert '=== batch/sub/deep-rule.md ===' in joined
    assert '# Deep Rule' in joined


def test_read_rules_follows_symlinked_files(gated_home: Path, tmp_path: Path) -> None:
    external = tmp_path / 'external.md'
    external.write_text('# External\n', encoding='utf-8')
    batch = gated_home / 'lore' / 'rules_md' / 'linked'
    batch.mkdir(parents=True)
    (batch / 'external.md').symlink_to(external)

    select_rules([])
    joined = '\n'.join(_drain())
    assert '=== linked/external.md ===' in joined
    assert '# External' in joined


def test_read_rules_empty_dir(gated_home: Path) -> None:
    rules_dir = gated_home / 'lore' / 'rules_md'
    shutil.rmtree(rules_dir)
    rules_dir.mkdir()
    select_rules([])
    result = read_rules()
    assert '=== DONE' in result
    assert 'No rule files to read' in result


def test_read_rules_missing_dir(gated_home: Path) -> None:
    shutil.rmtree(gated_home / 'lore' / 'rules_md')
    select_rules([])
    result = read_rules()
    assert '=== DONE' in result
