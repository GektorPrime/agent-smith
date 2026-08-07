from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

import agent_smith.config as config_module
from agent_smith.kdb import audit_json, sync_kb
from agent_smith.mcp.kb import query as kb_query


class _FakeEmbeddingModel:
    """Minimal fastembed TextEmbedding stand-in."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def embed(self, texts: list[str]):
        for text in texts:
            seed = float((sum(ord(ch) for ch in text) % 50) + 1)
            # Must be exactly 384 floats to match _EMBEDDING_DIMENSIONS
            vec = [seed / float(i + 1) for i in range(384)]
            yield _Vec(vec)


class _Vec:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def __len__(self) -> int:
        return len(self._values)

    def tolist(self) -> list[float]:
        return self._values


def test_sync_empty_scenarios(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None

    scenarios_dir = tmp_agent_smith_home / 'lore' / 'json' / 'scenarios'
    (tmp_agent_smith_home / 'knowledge_base').mkdir(parents=True, exist_ok=True)
    for old_json in scenarios_dir.rglob('*.json'):
        old_json.unlink()

    sync_kb.sync()
    out = capsys.readouterr().out
    assert '[sync_kb] No scenario files found. Nothing to sync.' in out


def test_sync_with_scenario_creates_db(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None

    scenarios_dir = tmp_agent_smith_home / 'lore' / 'json' / 'scenarios'
    (tmp_agent_smith_home / 'knowledge_base').mkdir(parents=True, exist_ok=True)
    for old_json in scenarios_dir.rglob('*.json'):
        old_json.unlink()

    scenario_payload = [
        {
            'id': 'demo-reference-001',
            'type': 'reference',
            'source_file': 'lore/rules_md/demo.md',
            'section': 'Demo Section',
            'tags': ['core'],
            'severity': 'soft',
            'explanation': 'This is a valid reference scenario for integration testing. It ensures the sync path has at least one input object.',
            'bdd': {
                'given': 'a synced knowledge base',
                'when': 'a semantic query is executed',
                'then': 'results are returned without raising runtime errors',
            },
            'verbatim_rule': 'results are returned without raising runtime errors',
            'examples': {
                'correct': 'assert isinstance(results, list)',
                'incorrect': None,
            },
            'mcp_tool_hint': None,
        }
    ]
    (scenarios_dir / 'minimal.json').write_text(
        json.dumps(scenario_payload, indent=2) + '\n', encoding='utf-8'
    )

    # Patch EmbeddingClient to avoid downloading the real model
    class _FakeEmbeddingClient:
        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            result = []
            for text in texts:
                seed = float((sum(ord(ch) for ch in text) % 50) + 1)
                vec = [seed / float(i + 1) for i in range(384)]
                result.append(vec)
            return result

    def _fake_init_db(db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path))
        conn.execute(sync_kb._CREATE_SCENARIOS_TABLE)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_embeddings (
                id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL
            )
            """
        )
        conn.commit()
        return conn

    monkeypatch.setattr(sync_kb, 'EmbeddingClient', _FakeEmbeddingClient)
    monkeypatch.setattr(sync_kb, '_init_db', _fake_init_db)

    config_module._config = None
    sync_kb.sync()
    out = capsys.readouterr().out

    assert '[sync_kb] Sync complete.' in out
    db_path = tmp_agent_smith_home / 'knowledge_base' / 'rules.db'
    assert db_path.exists()
    last_sync = tmp_agent_smith_home / 'knowledge_base' / 'last_sync.json'
    assert last_sync.exists()


def test_query_returns_list_or_error_when_no_db(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    # Reset lazy-loaded KB state
    kb_query._loaded = False
    kb_query._db_conn = None
    kb_query._embedding_model = None
    config_module._config = None

    result_str = kb_query.query_rules('test query')
    result = json.loads(result_str)
    # Either a list (if DB exists) or an error dict
    assert isinstance(result, (list, dict))


def test_kb_ready_does_not_load_embedding_model(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None
    db_path = tmp_agent_smith_home / 'knowledge_base' / 'rules.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sync_kb._init_db(db_path)
    sync_kb._upsert_scenario(
        conn,
        {
            'id': 'ready-rule-001',
            'type': 'rule',
            'source_file': 'lore/rules_md/ready.md',
            'section': 'Readiness',
            'tags': ['test'],
            'severity': 'hard',
            'bdd': {
                'given': 'indexed content',
                'when': 'readiness is checked',
                'then': 'the model is not loaded',
            },
            'verbatim_rule': 'the model is not loaded',
        },
        [0.0] * 384,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        kb_query,
        '_load_embedding_model',
        lambda: (_ for _ in ()).throw(AssertionError('model loaded')),
    )

    assert kb_query.kb_ready() is True


def test_db_available_requires_embedding_model(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None
    db_path = tmp_agent_smith_home / 'knowledge_base' / 'rules.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sync_kb._init_db(db_path)
    sync_kb._upsert_scenario(
        conn,
        {
            'id': 'model-rule-001',
            'type': 'rule',
            'source_file': 'lore/rules_md/model.md',
            'section': 'Model readiness',
            'tags': ['test'],
            'severity': 'hard',
            'bdd': {
                'given': 'a valid database',
                'when': 'the embedding model is unavailable',
                'then': 'knowledge mode is unavailable',
            },
            'verbatim_rule': 'knowledge mode is unavailable',
        },
        [0.0] * 384,
    )
    conn.commit()
    conn.close()
    kb_query.reset()
    monkeypatch.setattr(kb_query, '_load_embedding_model', lambda: None)

    assert kb_query.kb_ready() is True
    assert kb_query.db_available() is False


def test_reset_closes_cached_connection() -> None:
    class _Connection:
        closed = False

        def close(self) -> None:
            self.closed = True

    conn = _Connection()
    kb_query._db_conn = conn
    kb_query._embedding_model = object()
    kb_query._loaded = True

    kb_query.reset()

    assert conn.closed is True
    assert kb_query._db_conn is None
    assert kb_query._embedding_model is None
    assert kb_query._loaded is False


def test_audit_with_no_files(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None

    exit_code = audit_json.main()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert 'Checked 0 files.' in out


def _scenario_with_then(then: str) -> dict:
    """A schema-valid rule scenario whose only variable is bdd.then / verbatim_rule.

    verbatim_rule is kept token-aligned with `then` so the scenario trips only
    the [BAD-THEN] check (never [VR-MISMATCH]) when `then` is a smell.
    """
    return {
        'id': 'demo-badthen-001',
        'type': 'rule',
        'source_file': 'lore/rules_md/demo.md',
        'section': 'Demo Section',
        'tags': ['core'],
        'severity': 'hard',
        'explanation': (
            'This explanation exists to satisfy the two-sentence minimum. '
            'It describes why the rule matters in enough words to pass SHORT-EXPL.'
        ),
        'bdd': {
            'given': 'a file under review',
            'when': 'the agent writes code',
            'then': then,
        },
        'verbatim_rule': then,
        'examples': {'correct': 'x == 1', 'incorrect': '1 == x'},
        'mcp_tool_hint': None,
    }


class TestBadThenHelper:
    """Unit tests for the pure _bad_then() heuristic."""

    def test_empty_then_is_not_flagged(self) -> None:
        assert audit_json._bad_then('') is None
        assert audit_json._bad_then('   ') is None

    def test_normative_concrete_then_passes(self) -> None:
        assert (
            audit_json._bad_then('the fixture MUST declare an explicit scope')
            is None
        )
        assert (
            audit_json._bad_then('helper functions MUST NOT be nested in classes')
            is None
        )
        assert audit_json._bad_then('the value shall never be hard-coded') is None
        assert audit_json._bad_then('a docstring is required on public methods') is None

    def test_cross_reference_then_is_flagged(self) -> None:
        for then in (
            'assertion order — as described in the coding convention',
            'apply the naming rule as defined above',
            'behave per the section on fixtures',
            'follow the conventions described elsewhere',
            'refer to the rule for account selection',
            'this behaviour is covered in the map document',
        ):
            reason = audit_json._bad_then(then)
            assert reason is not None, then
            assert reason.startswith('cross-reference'), (then, reason)

    def test_non_normative_then_is_flagged(self) -> None:
        # No MUST / MUST NOT / required / prohibited token, no cross-ref phrase.
        reason = audit_json._bad_then('the helper is placed in a module')
        assert reason is not None
        assert reason.startswith('non-normative')

    def test_cross_reference_takes_precedence_over_non_normative(self) -> None:
        # A cross-ref phrase that also lacks a normative token must report (i), not (ii).
        reason = audit_json._bad_then('as described above in the convention')
        assert reason is not None
        assert reason.startswith('cross-reference')


def test_audit_flags_bad_then(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path
) -> None:
    """run_audit emits [BAD-THEN] for a cross-referencing THEN clause."""
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None

    scenarios_dir = tmp_agent_smith_home / 'lore' / 'json' / 'scenarios'
    for old_json in scenarios_dir.rglob('*.json'):
        old_json.unlink()
    (scenarios_dir / 'bad.json').write_text(
        json.dumps(
            [_scenario_with_then('assertion order — as described in the convention')]
        ),
        encoding='utf-8',
    )

    count, issues = audit_json.run_audit()
    assert count == 1
    bad_then = [i for i in issues if '[BAD-THEN]' in i]
    assert len(bad_then) == 1
    assert 'demo-badthen-001' in bad_then[0]
    assert 'cross-reference' in bad_then[0]


def test_audit_clean_then_produces_no_bad_then(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path
) -> None:
    """A concrete, normative THEN produces no [BAD-THEN] issue."""
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None

    scenarios_dir = tmp_agent_smith_home / 'lore' / 'json' / 'scenarios'
    for old_json in scenarios_dir.rglob('*.json'):
        old_json.unlink()
    (scenarios_dir / 'good.json').write_text(
        json.dumps(
            [_scenario_with_then('the fixture MUST declare an explicit scope')]
        ),
        encoding='utf-8',
    )

    count, issues = audit_json.run_audit()
    assert count == 1
    assert not [i for i in issues if '[BAD-THEN]' in i]


def test_audit_bad_then_skips_non_rule_types(
    monkeypatch, tmp_agent_smith_home: Path, tmp_path: Path
) -> None:
    """[BAD-THEN] only applies to type=rule, matching VR-MISMATCH/SHORT-EXPL scope."""
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None

    scenario = _scenario_with_then('the helper is placed in a module')
    scenario['id'] = 'demo-reference-002'
    scenario['type'] = 'reference'

    scenarios_dir = tmp_agent_smith_home / 'lore' / 'json' / 'scenarios'
    for old_json in scenarios_dir.rglob('*.json'):
        old_json.unlink()
    (scenarios_dir / 'ref.json').write_text(
        json.dumps([scenario]), encoding='utf-8'
    )

    count, issues = audit_json.run_audit()
    assert count == 1
    assert not [i for i in issues if '[BAD-THEN]' in i]
