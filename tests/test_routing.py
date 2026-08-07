from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_smith.mcp.gates import readers
from agent_smith.mcp.gates.readers import (
    read_routing_table,
    select_rules,
)


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    readers._state.reset()
    yield
    readers._state.reset()


def _mandated_rel(gated_home: Path) -> set[str]:
    rules_dir = gated_home / 'lore' / 'rules_md'
    return {p.relative_to(rules_dir).as_posix() for p in readers._state.mandated}


# --- read_routing_table ---


def test_read_routing_table_present(gated_home: Path) -> None:
    result = read_routing_table()
    assert result.startswith('=== rules_routing_table.md ===')
    assert 'API work' in result


def test_read_routing_table_absent(gated_home: Path) -> None:
    (gated_home / 'lore' / 'rules_routing_table.md').unlink()
    result = read_routing_table()
    assert 'absent' in result
    assert 'empty list' in result


# --- select_rules ---


def test_select_valid_subset_pins_subset_plus_core(gated_home: Path) -> None:
    msg = select_rules(['marked-rule.md'])
    assert 'Pinned' in msg
    assert _mandated_rel(gated_home) == {'core/core-rule.md', 'marked-rule.md'}


def test_select_empty_pins_all(gated_home: Path) -> None:
    select_rules([])
    assert _mandated_rel(gated_home) == {
        'core/core-rule.md',
        'marked-rule.md',
        'unmarked-rule.md',
    }


def test_select_invalid_path_hard_falls_back_to_all(gated_home: Path) -> None:
    select_rules(['marked-rule.md', 'does-not-exist.md'])
    # Any invalid entry -> ALL rules, not the valid subset.
    assert _mandated_rel(gated_home) == {
        'core/core-rule.md',
        'marked-rule.md',
        'unmarked-rule.md',
    }


def test_select_dedups_selection_and_core(gated_home: Path) -> None:
    # Selecting the core file explicitly must not duplicate it.
    select_rules(['core/core-rule.md', 'marked-rule.md', 'marked-rule.md'])
    mandated = [
        p.relative_to(gated_home / 'lore' / 'rules_md').as_posix()
        for p in readers._state.mandated
    ]
    assert mandated.count('core/core-rule.md') == 1
    assert mandated.count('marked-rule.md') == 1


def test_select_core_always_included(gated_home: Path) -> None:
    select_rules(['unmarked-rule.md'])
    assert 'core/core-rule.md' in _mandated_rel(gated_home)


def test_select_tolerates_rules_md_prefix_and_dotslash(gated_home: Path) -> None:
    select_rules(['./rules_md/marked-rule.md'])
    assert 'marked-rule.md' in _mandated_rel(gated_home)


def test_select_is_idempotent_after_pin(gated_home: Path) -> None:
    select_rules(['marked-rule.md'])
    before = list(readers._state.mandated)
    msg = select_rules([])  # would otherwise widen to all
    assert 'already pinned' in msg
    assert readers._state.mandated == before


def test_select_absent_table_still_falls_back_to_all(gated_home: Path) -> None:
    # Table absence is a host concern; select_rules([]) already yields all.
    (gated_home / 'lore' / 'rules_routing_table.md').unlink()
    select_rules([])
    assert _mandated_rel(gated_home) == {
        'core/core-rule.md',
        'marked-rule.md',
        'unmarked-rule.md',
    }
