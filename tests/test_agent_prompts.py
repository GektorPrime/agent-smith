"""Prompt/static-contract tests for the opencode-pty wiring across shipped
agents.

These are string-level checks against the agent Markdown+frontmatter files
themselves (wireframe = release template, .agent-smith = this repo's active
copy). They do not exercise a live OpenCode runtime — see the "Runtime
compatibility gate" note in README.md for what remains unverified without a
running OpenCode instance and a restart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TREES = ['wireframe/opencode', '.agent-smith/opencode']

# Execution-capable shipped agents: wired directly to pty_* tools.
EXECUTION_CAPABLE = [
    'agents/architect.md',
    'agents/subagents/executor.md',
    'agents/subagents/oracle.md',
    'agents/subagents/inquisitor.md',
    'agents/analyst.md',
]

# Shipped agents that must not execute commands / must not use PTY.
PTY_DENIED = [
    'agents/subagents/basher.md',
    'agents/subagents/librarian.md',
]

ALL_AGENTS = EXECUTION_CAPABLE + PTY_DENIED


def _frontmatter(tree: str, rel_path: str) -> str:
    text = (REPO_ROOT / tree / rel_path).read_text(encoding='utf-8')
    parts = text.split('---', 2)
    assert len(parts) >= 3, f'{tree}/{rel_path} is missing a frontmatter block'
    return parts[1]


def _full_text(tree: str, rel_path: str) -> str:
    return (REPO_ROOT / tree / rel_path).read_text(encoding='utf-8')


@pytest.mark.parametrize('tree', TREES)
@pytest.mark.parametrize('rel_path', EXECUTION_CAPABLE)
def test_execution_capable_agent_permits_pty(tree: str, rel_path: str) -> None:
    frontmatter = _frontmatter(tree, rel_path)
    assert '"pty_*": allow' in frontmatter


@pytest.mark.parametrize('tree', TREES)
@pytest.mark.parametrize('rel_path', PTY_DENIED)
def test_non_execution_agent_denies_pty(tree: str, rel_path: str) -> None:
    frontmatter = _frontmatter(tree, rel_path)
    assert '"pty_*": deny' in frontmatter


@pytest.mark.parametrize('tree', TREES)
@pytest.mark.parametrize('rel_path', EXECUTION_CAPABLE)
def test_execution_capable_agents_no_longer_delegate_to_basher(
    tree: str, rel_path: str
) -> None:
    frontmatter = _frontmatter(tree, rel_path)
    assert '"basher": allow' not in frontmatter
    assert '"basher": deny' in frontmatter


@pytest.mark.parametrize('tree', TREES)
@pytest.mark.parametrize('rel_path', EXECUTION_CAPABLE)
def test_execution_capable_agents_have_no_basher_receipt_language(
    tree: str, rel_path: str
) -> None:
    text = _full_text(tree, rel_path)
    assert 'Basher receipt discipline' not in text
    assert '---BASHER-RESULT---' not in text
    assert 'delegate to **Basher**' not in text


@pytest.mark.parametrize('tree', TREES)
@pytest.mark.parametrize('rel_path', EXECUTION_CAPABLE)
def test_execution_capable_agents_document_pty_lifecycle_evidence(
    tree: str, rel_path: str
) -> None:
    text = _full_text(tree, rel_path)
    assert 'PTY lifecycle evidence' in text
    assert 'is asynchronous' in text
    # The compatibility-gate requirement: subagent tasks must not claim
    # completion from a `running` status.
    assert 'MUST NOT' in text
    assert 'while the status is `running`' in text
    # Bounded reads and cleanup-after-evidence-consumed requirements.
    assert 'pty_read' in text and ('offset' in text or 'bounded' in text)
    assert 'cleanup=true' in text
    assert 'rolling in-memory buffer' in text


@pytest.mark.parametrize('tree', TREES)
def test_basher_still_ships_output_contract(tree: str) -> None:
    text = _full_text(tree, 'agents/subagents/basher.md')
    assert '---BASHER-RESULT---' in text
    assert 'EXIT: <integer exit code>' in text


@pytest.mark.parametrize('tree', TREES)
def test_basher_native_bash_still_allowed(tree: str) -> None:
    frontmatter = _frontmatter(tree, 'agents/subagents/basher.md')
    assert 'bash: allow' in frontmatter


@pytest.mark.parametrize('tree', TREES)
def test_librarian_denies_bash_and_basher_delegation(tree: str) -> None:
    frontmatter = _frontmatter(tree, 'agents/subagents/librarian.md')
    assert 'bash: deny' in frontmatter
    assert '"basher": deny' in frontmatter


@pytest.mark.parametrize('tree', TREES)
def test_architect_and_executor_keep_native_bash_denied(tree: str) -> None:
    for rel_path in ('agents/architect.md', 'agents/subagents/executor.md'):
        frontmatter = _frontmatter(tree, rel_path)
        assert 'bash: deny' in frontmatter


@pytest.mark.parametrize('tree', TREES)
def test_oracle_and_inquisitor_keep_native_bash_denied(tree: str) -> None:
    for rel_path in ('agents/subagents/oracle.md', 'agents/subagents/inquisitor.md'):
        frontmatter = _frontmatter(tree, rel_path)
        assert 'bash: deny' in frontmatter


@pytest.mark.parametrize('tree', TREES)
def test_architect_still_delegates_code_edits_to_executor(tree: str) -> None:
    frontmatter = _frontmatter(tree, 'agents/architect.md')
    assert '"executor": allow' in frontmatter
    text = _full_text(tree, 'agents/architect.md')
    assert 'delegating to `executor`' in text


@pytest.mark.parametrize('tree', TREES)
def test_wireframe_and_active_trees_agree_on_pty_wiring(tree: str) -> None:
    """The two trees must agree on every pty_*/basher permission line (they
    are expected to diverge only on model/temperature/top_p fields)."""
    other = '.agent-smith/opencode' if tree == 'wireframe/opencode' else 'wireframe/opencode'
    for rel_path in ALL_AGENTS:
        frontmatter_a = _frontmatter(tree, rel_path)
        frontmatter_b = _frontmatter(other, rel_path)
        for line in frontmatter_a.splitlines():
            stripped = line.strip()
            if stripped.startswith('"pty_*"') or stripped.startswith('"basher"'):
                assert stripped in frontmatter_b, (
                    f'{rel_path}: {stripped!r} present in {tree} but not {other}'
                )
