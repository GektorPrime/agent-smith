from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

import pytest

from agent_smith.kdb.tools import export_tensorboard


def _build_rules_db(home: Path, scenario_count: int = 3) -> Path:
    import sqlite_vec

    db_dir = home / 'knowledge_base'
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / 'rules.db'

    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.execute(
        """
        CREATE TABLE scenarios (
            id            TEXT PRIMARY KEY,
            type          TEXT NOT NULL,
            source_file   TEXT NOT NULL,
            section       TEXT NOT NULL,
            tags          TEXT NOT NULL,
            severity      TEXT NOT NULL,
            bdd_given     TEXT NOT NULL,
            bdd_when      TEXT NOT NULL,
            bdd_then      TEXT NOT NULL,
            verbatim_rule TEXT NOT NULL,
            json_blob     TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE scenario_embeddings USING vec0(
            id            TEXT PRIMARY KEY,
            embedding     FLOAT[384]
        )
        """
    )

    for i in range(scenario_count):
        sid = f'demo-rule-{i:03d}'
        blob = {
            'explanation': f'Explanation {i}.\nSecond line.',
            'bdd': {'and': [f'and-clause-{i}'], 'but': []},
            'mcp_tool_hint': None,
        }
        conn.execute(
            'INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                sid,
                'rule',
                'lore/rules_md/demo.md',
                'Demo Section',
                json.dumps(['core', 'demo']),
                'hard',
                'a precondition',
                'a trigger',
                f'a\tconstraint {i}',
                f'verbatim rule {i}',
                json.dumps(blob),
            ),
        )
        vec = [float(i + 1) / (j + 1) for j in range(384)]
        conn.execute(
            'INSERT INTO scenario_embeddings (id, embedding) VALUES (?, ?)',
            (sid, struct.pack('384f', *vec)),
        )

    conn.commit()
    conn.close()
    return db_path


def test_export_tensorboard_writes_tsv_files(
    monkeypatch, gated_home: Path, capsys
) -> None:
    _build_rules_db(gated_home)

    export_tensorboard.main()
    out = capsys.readouterr().out

    assert 'Exported 3 scenarios.' in out

    tensors = gated_home / 'knowledge_base' / 'tmp' / 'tensors.tsv'
    metadata = gated_home / 'knowledge_base' / 'tmp' / 'metadata.tsv'
    assert tensors.is_file()
    assert metadata.is_file()

    tensor_lines = tensors.read_text(encoding='utf-8').splitlines()
    assert len(tensor_lines) == 3  # no header
    assert all(len(line.split('\t')) == 384 for line in tensor_lines)

    metadata_lines = metadata.read_text(encoding='utf-8').splitlines()
    assert len(metadata_lines) == 4  # header + 3 rows
    header = metadata_lines[0].split('\t')
    assert header[0] == 'id'
    assert 'verbatim_rule' in header

    # TSV-safety: tabs/newlines in values are cleaned
    row = dict(zip(header, metadata_lines[1].split('\t')))
    assert row['bdd_then'] == 'a constraint 0'
    assert row['tags'] == 'core, demo'
    assert row['bdd_and'] == 'and-clause-0'


def test_export_tensorboard_missing_db_exits(monkeypatch, gated_home: Path, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        export_tensorboard.main()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert 'rules.db not found' in err
    assert 'agent-smith-kb-sync' in err


def test_visualize_kb_module_imports_without_viz_deps() -> None:
    """Module import must stay light: heavy deps load lazily inside main()."""
    from agent_smith.kdb.tools import visualize_kb

    assert callable(visualize_kb.main)
    assert callable(visualize_kb._require_viz_deps)


def test_visualize_kb_derive_test_type() -> None:
    from agent_smith.kdb.tools.visualize_kb import _derive_test_type

    assert _derive_test_type(json.dumps(['core', 'test_type:api-public'])) == 'api-public'
    assert _derive_test_type(json.dumps(['core'])) == 'core'
    assert _derive_test_type('not-json') == 'core'
