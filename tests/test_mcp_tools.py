from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import agent_smith.config as config_module
from agent_smith.kdb import sync_kb
from agent_smith.mcp.kb import query as kb_query
from agent_smith.mcp.gates import readers
from agent_smith.mcp.server import (
    agent_smith_check_db,
    agent_smith_handoff,
    agent_smith_init,
    agent_smith_read_entry_point,
    agent_smith_read_persona,
    agent_smith_read_routing_table,
    agent_smith_read_rules,
    agent_smith_select_rules,
    mcp,
)

RULES_HANDOFF_OPENING = 'Reading protocol acknowledged and complete.'
KNOWLEDGE_HANDOFF_OPENING = 'Initialization complete.'
CALL_TO_ACTION = 'Begin executing that task immediately using the tools available in your current role.'
NO_STALL_CLAUSE = 'Do not emit another text-only response until the task is complete'


T = TypeVar('T')


def setup_function() -> None:
    kb_query.reset()
    readers._state.reset()


def teardown_function() -> None:
    kb_query.reset()
    readers._state.reset()


def _drain_reads() -> None:
    """Drive read_rules to DONE so the handoff ledger is complete."""
    guard = 0
    while True:
        guard += 1
        assert guard < 100
        if '=== DONE' in run(agent_smith_read_rules()):
            break


def _collect_tokens(home: Path) -> list[str]:
    """Return every current gate token under the gated home."""
    tokens: list[str] = []
    for path in home.rglob('*.md'):
        match = re.search(r'AGENT GATE: Include the phrase "([^"]+)"', path.read_text(encoding='utf-8'))
        if match:
            tokens.append(match.group(1))
    return tokens


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _set_protocol(monkeypatch, protocol: str) -> None:
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', protocol)
    config_module._config = None


def _make_db(monkeypatch, home: Path) -> None:
    (home / 'knowledge_base').mkdir(exist_ok=True)
    conn = sync_kb._init_db(home / 'knowledge_base' / 'rules.db')
    sync_kb._upsert_scenario(
        conn,
        {
            'id': 'test-rule-001',
            'type': 'rule',
            'source_file': 'lore/rules_md/test.md',
            'section': 'Test rule',
            'tags': ['test'],
            'severity': 'hard',
            'bdd': {
                'given': 'a valid knowledge base',
                'when': 'readiness is checked',
                'then': 'the database is available',
            },
            'verbatim_rule': 'the database is available',
        },
        [0.0] * 384,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(kb_query, '_load_embedding_model', lambda: object())


def _current_token(home: Path) -> str:
    match = re.search(
        r'AGENT GATE: Include the phrase "([^"]+)"',
        (home / 'entry-point.md').read_text(encoding='utf-8'),
    )
    assert match
    return match.group(1)


def test_mcp_local_names_compose_with_opencode_server_prefix() -> None:
    local_names = {tool.name for tool in run(mcp.list_tools())}
    assert local_names == {
        'check_db',
        'init',
        'handoff',
        'query_rules',
        'read_entry_point',
        'read_rules',
        'read_routing_table',
        'select_rules',
        'read_persona',
        'ast_analyze_structure',
        'ast_class_outline',
        'ast_list_imports',
        'ast_find_definitions',
        'ast_search',
    }
    opencode_names = {f'agent_smith_{name}' for name in local_names}
    assert 'agent_smith_init' in opencode_names
    assert 'agent_smith_query_rules' in opencode_names
    assert 'agent_smith_ast_search' in opencode_names
    assert all('agent_smith_agent_smith_' not in name for name in opencode_names)


def test_check_db_without_db_reports_unavailable(monkeypatch, gated_home: Path) -> None:
    result = run(agent_smith_check_db())
    assert isinstance(result, dict)
    assert result['available'] is False
    assert str(gated_home / 'knowledge_base' / 'rules.db') == result['path']


def test_check_db_with_db_reports_available(monkeypatch, gated_home: Path) -> None:
    _make_db(monkeypatch, gated_home)
    result = run(agent_smith_check_db())
    assert result['available'] is True


def test_check_db_with_empty_or_corrupt_db_reports_unavailable(
    monkeypatch, gated_home: Path
) -> None:
    db_dir = gated_home / 'knowledge_base'
    db_dir.mkdir(exist_ok=True)
    db_path = db_dir / 'rules.db'

    sqlite3.connect(db_path).close()
    assert run(agent_smith_check_db())['available'] is False

    db_path.write_text('not a sqlite database', encoding='utf-8')
    assert run(agent_smith_check_db())['available'] is False


# --- init: {rules, knowledge} x {db-available, db-missing} ---


def test_init_rules_protocol(monkeypatch, gated_home: Path) -> None:
    _set_protocol(monkeypatch, 'rules')
    init_text = run(agent_smith_init())
    assert isinstance(init_text, str)

    assert init_text.startswith('=== GATE REGENERATION ===')
    assert 'UPDATED entry-point.md: gate phrase regenerated.' in init_text
    assert '=== NEXT STEPS ===' in init_text
    assert 'agent_smith_read_entry_point' in init_text
    assert 'agent_smith_read_persona' in init_text
    assert 'agent_smith_read_routing_table' in init_text
    assert 'agent_smith_select_rules' in init_text
    assert 'agent_smith_read_rules' in init_text
    assert 'agent_smith_handoff' in init_text
    assert 'acknowledged_tokens' in init_text


def test_init_rules_protocol_with_db_still_reads_rules(
    monkeypatch, gated_home: Path
) -> None:
    _set_protocol(monkeypatch, 'rules')
    _make_db(monkeypatch, gated_home)
    init_text = run(agent_smith_init())
    assert init_text.startswith('=== GATE REGENERATION ===')
    assert '=== NEXT STEPS ===' in init_text


def test_init_knowledge_protocol_db_missing_falls_back_to_rules(
    monkeypatch, gated_home: Path
) -> None:
    _set_protocol(monkeypatch, 'knowledge')
    init_text = run(agent_smith_init())
    assert init_text.startswith('=== GATE REGENERATION ===')
    assert '=== NEXT STEPS ===' in init_text


def test_init_knowledge_protocol_db_available(monkeypatch, gated_home: Path) -> None:
    _set_protocol(monkeypatch, 'knowledge')
    _make_db(monkeypatch, gated_home)

    token_before = _current_token(gated_home)
    init_text = run(agent_smith_init())

    assert init_text == (
        '=== AGENT SMITH ===\n'
        'Knowledge base is available.\n'
        'Use agent_smith_query_rules on demand when you need rule context.\n'
        'Call agent_smith_handoff to begin the task.'
    )
    # Gates still rotate to keep the fallback machinery live
    assert _current_token(gated_home) != token_before


# --- handoff: {rules, knowledge} x {db-available, db-missing} ---


def test_handoff_rules_protocol(monkeypatch, gated_home: Path) -> None:
    _set_protocol(monkeypatch, 'rules')
    run(agent_smith_select_rules([]))
    _drain_reads()
    handoff_text = run(agent_smith_handoff(_collect_tokens(gated_home)))
    assert handoff_text.startswith(RULES_HANDOFF_OPENING)
    assert CALL_TO_ACTION in handoff_text
    assert NO_STALL_CLAUSE in handoff_text
    assert handoff_text.endswith('If no task has been stated yet, ask the user what they want to do.')


def test_handoff_rules_refused_before_reads(monkeypatch, gated_home: Path) -> None:
    _set_protocol(monkeypatch, 'rules')
    handoff_text = run(agent_smith_handoff())
    assert handoff_text.startswith('HANDOFF REFUSED')


def test_handoff_rules_refused_when_tokens_missing(
    monkeypatch, gated_home: Path
) -> None:
    _set_protocol(monkeypatch, 'rules')
    run(agent_smith_select_rules([]))
    _drain_reads()
    handoff_text = run(agent_smith_handoff([]))
    assert handoff_text.startswith('HANDOFF REFUSED')
    assert 'gate phrases' in handoff_text


def test_handoff_rules_refused_when_reads_incomplete(
    monkeypatch, gated_home: Path
) -> None:
    _set_protocol(monkeypatch, 'rules')
    run(agent_smith_select_rules([]))
    run(agent_smith_read_rules())  # serve only one file
    handoff_text = run(agent_smith_handoff(_collect_tokens(gated_home)))
    assert handoff_text.startswith('HANDOFF REFUSED')
    assert 'not yet served' in handoff_text


def test_handoff_knowledge_protocol_db_missing_uses_rules_text(
    monkeypatch, gated_home: Path
) -> None:
    _set_protocol(monkeypatch, 'knowledge')
    run(agent_smith_select_rules([]))
    _drain_reads()
    handoff_text = run(agent_smith_handoff(_collect_tokens(gated_home)))
    assert handoff_text.startswith(RULES_HANDOFF_OPENING)
    assert CALL_TO_ACTION in handoff_text


def test_handoff_knowledge_protocol_db_available(monkeypatch, gated_home: Path) -> None:
    _set_protocol(monkeypatch, 'knowledge')
    _make_db(monkeypatch, gated_home)
    handoff_text = run(agent_smith_handoff())
    assert handoff_text.startswith(KNOWLEDGE_HANDOFF_OPENING)
    assert 'Use agent_smith_query_rules on demand when you need rule context.' in handoff_text
    assert CALL_TO_ACTION in handoff_text
    assert NO_STALL_CLAUSE in handoff_text


# --- readers exposed as tools ---


def test_read_entry_point_success_and_missing_file(monkeypatch, gated_home: Path) -> None:
    content = run(agent_smith_read_entry_point())
    assert isinstance(content, str)
    assert content.startswith('=== entry-point.md ===\n\n')
    assert '# Entry' in content

    (gated_home / 'entry-point.md').unlink()
    error_text = run(agent_smith_read_entry_point())
    assert 'ERROR: File not found:' in error_text


def test_read_rules_serves_one_file_then_next(monkeypatch, gated_home: Path) -> None:
    run(agent_smith_select_rules([]))
    first = run(agent_smith_read_rules())
    # One file served, with a NEXT directive (more remain).
    assert first.count('=== ') == 2
    assert '=== NEXT' in first


def test_read_routing_table_tool(monkeypatch, gated_home: Path) -> None:
    result = run(agent_smith_read_routing_table())
    assert result.startswith('=== rules_routing_table.md ===')


def test_select_rules_tool_pins(monkeypatch, gated_home: Path) -> None:
    result = run(agent_smith_select_rules(['marked-rule.md']))
    assert 'Pinned' in result


def test_read_persona_present(monkeypatch, gated_home: Path) -> None:
    persona_text = run(agent_smith_read_persona())
    assert persona_text.startswith('=== persona.md ===\n\n')
    assert '# Persona' in persona_text
