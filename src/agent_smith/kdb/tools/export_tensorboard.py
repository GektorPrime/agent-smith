#!/usr/bin/env python3
"""
export_tensorboard.py — Export Agent Smith knowledge base to TensorBoard
Embedding Projector format.

Reads rules.db and writes two TSV files to <AGENT_SMITH_HOME>/knowledge_base/tmp/:
  tensors.tsv   — one row per scenario, 384 tab-separated floats, no header
  metadata.tsv  — one row per scenario, all metadata fields, header row first

Usage:
    agent-smith-kb-export-tensorboard

Then load in TensorBoard Embedding Projector:
    1. Open https://projector.tensorflow.org/
    2. Click "Load" in the left panel
    3. Upload tensors.tsv as "Vectors"
    4. Upload metadata.tsv as "Metadata"

Requires:
    sqlite-vec — installed with the core agent_smith package.
    No API key, model download, or network connection needed.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import struct
import sys
from pathlib import Path

from agent_smith.config import get_config

try:
    import sqlite_vec  # type: ignore[import-untyped]
except ModuleNotFoundError:
    print(
        '[export_tensorboard] ERROR: sqlite_vec is not installed.\n'
        '  Install the agent_smith package (sqlite-vec is a core dependency):\n'
        "    pip install agent_smith\n"
        '  Or run via uvx:\n'
        "    uvx --from ~/.agent-smith-tool/<version> "
        'agent-smith-kb-export-tensorboard',
        file=sys.stderr,
    )
    sys.exit(1)

_EMBEDDING_DIM = 384

# Metadata columns written to metadata.tsv (in order).
# Values are derived in _build_metadata_row().
_METADATA_COLUMNS = [
    'id',
    'type',
    'source_file',
    'section',
    'tags',
    'severity',
    'bdd_given',
    'bdd_when',
    'bdd_then',
    'bdd_and',
    'bdd_but',
    'verbatim_rule',
    'explanation',
    'mcp_tool_hint',
]

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _open_db() -> sqlite3.Connection:
    db_path = get_config().rules_db_path()
    if not db_path.exists():
        print(
            f'[export_tensorboard] ERROR: rules.db not found at {db_path}\n'
            '                     Run `agent-smith-kb-sync` first.',
            file=sys.stderr,
        )
        sys.exit(1)
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn


def _load_scenarios(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            id,
            type,
            source_file,
            section,
            tags,
            severity,
            bdd_given,
            bdd_when,
            bdd_then,
            verbatim_rule,
            json_blob
        FROM scenarios
        ORDER BY id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _load_vectors(conn: sqlite3.Connection) -> dict[str, list[float]]:
    rows = conn.execute(
        'SELECT id, embedding FROM scenario_embeddings ORDER BY id'
    ).fetchall()
    return {row[0]: list(struct.unpack(f'{_EMBEDDING_DIM}f', row[1])) for row in rows}


def _output_paths() -> tuple[Path, Path]:
    out_dir = get_config().home() / 'knowledge_base' / 'tmp'
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / 'tensors.tsv', out_dir / 'metadata.tsv'


# ---------------------------------------------------------------------------
# Metadata row builder
# ---------------------------------------------------------------------------


def _clean(value: str) -> str:
    """Replace tab and newline characters so values are TSV-safe."""
    return value.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')


def _build_metadata_row(scenario: dict) -> dict[str, str]:
    blob: dict = {}
    try:
        blob = json.loads(scenario.get('json_blob') or '{}')
    except (json.JSONDecodeError, TypeError):
        pass

    # tags: stored as JSON array in DB, rendered as comma-separated string
    tags_raw = scenario.get('tags', '[]')
    try:
        tags_list: list[str] = json.loads(tags_raw)
        tags_str = ', '.join(tags_list)
    except (json.JSONDecodeError, TypeError):
        tags_str = tags_raw

    # bdd.and / bdd.but: optional lists inside json_blob
    bdd: dict = blob.get('bdd', {})
    bdd_and = ' | '.join(bdd.get('and', []) or [])
    bdd_but = ' | '.join(bdd.get('but', []) or [])

    return {
        'id': _clean(scenario.get('id', '')),
        'type': _clean(scenario.get('type', '')),
        'source_file': _clean(scenario.get('source_file', '')),
        'section': _clean(scenario.get('section', '')),
        'tags': _clean(tags_str),
        'severity': _clean(scenario.get('severity', '')),
        'bdd_given': _clean(scenario.get('bdd_given', '')),
        'bdd_when': _clean(scenario.get('bdd_when', '')),
        'bdd_then': _clean(scenario.get('bdd_then', '')),
        'bdd_and': _clean(bdd_and),
        'bdd_but': _clean(bdd_but),
        'verbatim_rule': _clean(scenario.get('verbatim_rule', '')),
        'explanation': _clean(blob.get('explanation', '')),
        'mcp_tool_hint': _clean(blob.get('mcp_tool_hint', '') or ''),
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export() -> None:
    conn = _open_db()

    print('[export_tensorboard] Loading scenarios …')
    scenarios = _load_scenarios(conn)

    print('[export_tensorboard] Loading embeddings …')
    vectors = _load_vectors(conn)

    conn.close()

    # Sanity check: every scenario must have a vector
    missing = [s['id'] for s in scenarios if s['id'] not in vectors]
    if missing:
        print(
            f'[export_tensorboard] ERROR: {len(missing)} '
            f'scenario(s) have no embedding: '
            + ', '.join(missing[:5])
            + (' …' if len(missing) > 5 else ''),
            file=sys.stderr,
        )
        sys.exit(1)

    if len(scenarios) != len(vectors):
        print(
            f'[export_tensorboard] WARNING: scenarios={len(scenarios)}, '
            f'embeddings={len(vectors)} — counts differ.',
            file=sys.stderr,
        )

    tensors_path, metadata_path = _output_paths()

    # ------------------------------------------------------------------
    # Write tensors.tsv — 384 floats per row, tab-separated, NO header
    # ------------------------------------------------------------------
    with tensors_path.open('w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh, delimiter='\t', lineterminator='\n')
        for scenario in scenarios:
            vec = vectors[scenario['id']]
            writer.writerow([f'{v:.6g}' for v in vec])

    # ------------------------------------------------------------------
    # Write metadata.tsv — header row first, then one row per scenario
    # ------------------------------------------------------------------
    with metadata_path.open('w', encoding='utf-8', newline='') as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=_METADATA_COLUMNS,
            delimiter='\t',
            lineterminator='\n',
            extrasaction='ignore',
        )
        writer.writeheader()
        for scenario in scenarios:
            writer.writerow(_build_metadata_row(scenario))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    n = len(scenarios)
    print(f'\nExported {n} scenarios.')
    print(f'  tensors:  {tensors_path}')
    print(f'  metadata: {metadata_path}')
    print()
    print('Load in TensorBoard Embedding Projector:')
    print('  1. Open https://projector.tensorflow.org/')
    print('  2. Click "Load" in the left panel')
    print('  3. Upload tensors.tsv as "Vectors"')
    print('  4. Upload metadata.tsv as "Metadata"')


def main() -> None:
    from agent_smith.config import configure_cli_environment, ensure_sqlite_cli_runtime

    configure_cli_environment()
    ensure_sqlite_cli_runtime('agent-smith-kb-export-tensorboard')
    export()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    main()
