"""Stale-ID deletion coverage for the sync_kb pipeline.

Exercises the sweep at sync_kb.sync() that removes DB rows whose scenario IDs
no longer appear in any source file (deleted files or in-file ID renames), plus
the force-rebuild path. Uses the real _init_db (real sqlite-vec vec0 table) with
a deterministic fake EmbeddingClient so no model download is required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent_smith.config as config_module
from agent_smith.kdb import sync_kb


class _FakeEmbeddingClient:
    """Deterministic 384-float embeddings; no network, no model download."""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            seed = float((sum(ord(ch) for ch in text) % 50) + 1)
            result.append([seed / float(i + 1) for i in range(384)])
        return result


@pytest.fixture
def kb_home(
    monkeypatch: pytest.MonkeyPatch, tmp_agent_smith_home: Path, tmp_path: Path
) -> Path:
    """Wireframe home wired for knowledge protocol with an empty scenarios dir."""
    monkeypatch.setenv('AGENT_SMITH_HOME', str(tmp_agent_smith_home))
    monkeypatch.setenv('AGENT_SMITH_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('AGENT_SMITH_PROTOCOL', 'knowledge')
    config_module._config = None

    (tmp_agent_smith_home / 'knowledge_base').mkdir(parents=True, exist_ok=True)
    scenarios_dir = tmp_agent_smith_home / 'lore' / 'json' / 'scenarios'
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    for old_json in scenarios_dir.rglob('*.json'):
        old_json.unlink()

    monkeypatch.setattr(sync_kb, 'EmbeddingClient', _FakeEmbeddingClient)
    return tmp_agent_smith_home


def _scenario(scenario_id: str) -> dict:
    """A schema-valid rule scenario keyed only by id."""
    return {
        'id': scenario_id,
        'type': 'rule',
        'source_file': 'lore/rules_md/demo.md',
        'section': 'Demo Section',
        'tags': ['core'],
        'severity': 'hard',
        'explanation': (
            'This explanation satisfies the two-sentence minimum. '
            'It exists so the scenario is structurally valid for sync.'
        ),
        'bdd': {
            'given': 'a file under review',
            'when': 'the agent writes code',
            'then': 'the rule MUST be followed exactly',
        },
        'verbatim_rule': 'the rule MUST be followed exactly',
        'examples': {'correct': 'x == 1', 'incorrect': '1 == x'},
        'mcp_tool_hint': None,
    }


def _write(scenarios_dir: Path, filename: str, ids: list[str]) -> Path:
    path = scenarios_dir / filename
    path.write_text(
        json.dumps([_scenario(i) for i in ids], indent=2) + '\n', encoding='utf-8'
    )
    return path


def _scenarios_dir(home: Path) -> Path:
    return home / 'lore' / 'json' / 'scenarios'


def _open_db(home: Path):
    return sync_kb._init_db(home / 'knowledge_base' / 'rules.db')


def _db_ids(home: Path) -> set[str]:
    conn = _open_db(home)
    try:
        scenario_ids = {r[0] for r in conn.execute('SELECT id FROM scenarios')}
        return scenario_ids
    finally:
        conn.close()


def _embedding_ids(home: Path) -> set[str]:
    conn = _open_db(home)
    try:
        return {r[0] for r in conn.execute('SELECT id FROM scenario_embeddings')}
    finally:
        conn.close()


def test_deleted_file_sweeps_its_ids_from_both_tables(kb_home: Path) -> None:
    scenarios_dir = _scenarios_dir(kb_home)
    _write(scenarios_dir, 'a.json', ['demo-rule-001'])
    deletable = _write(scenarios_dir, 'b.json', ['demo-rule-002'])

    sync_kb.sync()
    assert _db_ids(kb_home) == {'demo-rule-001', 'demo-rule-002'}

    deletable.unlink()
    sync_kb.sync()

    assert _db_ids(kb_home) == {'demo-rule-001'}
    assert _embedding_ids(kb_home) == {'demo-rule-001'}


def test_in_file_id_rename_removes_old_id(kb_home: Path) -> None:
    scenarios_dir = _scenarios_dir(kb_home)
    _write(scenarios_dir, 'a.json', ['demo-rule-001'])
    sync_kb.sync()
    assert _db_ids(kb_home) == {'demo-rule-001'}

    # Same file, renamed ID -> old row must be swept, new row present.
    _write(scenarios_dir, 'a.json', ['demo-rule-999'])
    sync_kb.sync()

    assert _db_ids(kb_home) == {'demo-rule-999'}
    assert _embedding_ids(kb_home) == {'demo-rule-999'}


def test_both_tables_stay_consistent_after_sweep(kb_home: Path) -> None:
    scenarios_dir = _scenarios_dir(kb_home)
    _write(scenarios_dir, 'a.json', ['demo-rule-001', 'demo-rule-002'])
    doomed = _write(scenarios_dir, 'b.json', ['demo-rule-003'])
    sync_kb.sync()

    doomed.unlink()
    sync_kb.sync()

    # No orphan embedding rows: the two tables carry an identical ID set.
    assert _db_ids(kb_home) == _embedding_ids(kb_home)
    assert _db_ids(kb_home) == {'demo-rule-001', 'demo-rule-002'}


def test_no_stale_ids_is_a_noop(kb_home: Path, capsys) -> None:
    scenarios_dir = _scenarios_dir(kb_home)
    _write(scenarios_dir, 'a.json', ['demo-rule-001'])
    sync_kb.sync()
    capsys.readouterr()

    # Re-sync with an unchanged corpus: nothing removed, DB stable.
    sync_kb.sync()
    out = capsys.readouterr().out

    assert 'Removed' not in out
    assert _db_ids(kb_home) == {'demo-rule-001'}
    assert _embedding_ids(kb_home) == {'demo-rule-001'}


def test_deletion_only_sync_updates_last_sync(kb_home: Path) -> None:
    scenarios_dir = _scenarios_dir(kb_home)
    _write(scenarios_dir, 'a.json', ['demo-rule-001'])
    removable = _write(scenarios_dir, 'b.json', ['demo-rule-002'])
    sync_kb.sync()

    removable.unlink()
    # No changed files remain (a.json is unchanged) — pure deletion sync.
    result = sync_kb.sync()

    assert result['status'] == 'ok'
    last_sync = json.loads(
        (kb_home / 'knowledge_base' / 'last_sync.json').read_text(encoding='utf-8')
    )
    assert 'b.json' not in json.dumps(last_sync['file_hashes'])
    assert _db_ids(kb_home) == {'demo-rule-001'}


def test_force_rebuild_drops_absent_ids(kb_home: Path) -> None:
    scenarios_dir = _scenarios_dir(kb_home)
    _write(scenarios_dir, 'a.json', ['demo-rule-001', 'demo-rule-002'])
    sync_kb.sync()
    assert _db_ids(kb_home) == {'demo-rule-001', 'demo-rule-002'}

    # Corpus shrinks, then a forced full rebuild via the temp-DB atomic swap.
    _write(scenarios_dir, 'a.json', ['demo-rule-001'])
    sync_kb.sync(force=True)

    assert _db_ids(kb_home) == {'demo-rule-001'}
    assert _embedding_ids(kb_home) == {'demo-rule-001'}
