#!/usr/bin/env python3
"""
sync_kb.py — Knowledge Base sync pipeline for Agent Smith.

Reads BDD scenario JSON files from agent_smith/knowledge/scenarios/,
embeds them via fastembed (BAAI/bge-small-en-v1.5, local ONNX inference),
and upserts into a local sqlite-vec database (rules.db). Supports
incremental sync via hash comparison and full rebuild via --force.

Usage:
    python sync_kb.py              # incremental sync (changed files only)
    python sync_kb.py --force      # full rebuild from scratch
    python sync_kb.py --dry-run    # show what would change, write nothing

Requires:
    fastembed, sqlite-vec — see agent_smith/requirements.txt.
    No API key or network connection needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
import time
from pathlib import Path
from typing import Any

from agent_smith.config import get_config

# ---------------------------------------------------------------------------
# Embedding config
# ---------------------------------------------------------------------------

_EMBEDDING_MODEL = 'BAAI/bge-small-en-v1.5'
_EMBEDDING_DIMENSIONS = 384
_EMBEDDING_PHASE = 2

# fastembed handles batching internally; this controls our chunking for
# progress reporting only.
_EMBEDDING_BATCH_SIZE = 100

# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

_CREATE_SCENARIOS_TABLE = """
CREATE TABLE IF NOT EXISTS scenarios (
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
);
"""

_CREATE_EMBEDDINGS_TABLE = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS scenario_embeddings USING vec0(
    id            TEXT PRIMARY KEY,
    embedding     FLOAT[{_EMBEDDING_DIMENSIONS}]
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    """Return hex SHA-256 of a file, normalised to UTF-8 + LF line endings.

    Normalisation ensures hashes are identical across platforms regardless of
    git core.autocrlf settings or editor line-ending differences.
    """
    h = hashlib.sha256()
    h.update(path.read_text(encoding='utf-8').replace('\r\n', '\n').encode('utf-8'))
    return f'sha256:{h.hexdigest()}'


def _load_last_sync() -> dict[str, Any]:
    """Load last_sync.json, returning empty dict if absent."""
    last_sync_path = get_config().home() / 'knowledge_base' / 'last_sync.json'
    if last_sync_path.exists():
        return json.loads(last_sync_path.read_text(encoding='utf-8'))
    return {}


def _save_last_sync(data: dict[str, Any]) -> None:
    """Write last_sync.json atomically."""
    last_sync_path = get_config().home() / 'knowledge_base' / 'last_sync.json'
    tmp = last_sync_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + '\n', encoding='utf-8')
    tmp.replace(last_sync_path)


def _discover_scenario_files() -> list[Path]:
    """Find all .json files under scenarios/ and exemplars/."""
    files: list[Path] = []
    scenarios_dir = get_config().scenarios_path()
    exemplars_dir = get_config().exemplars_path()
    for root in (scenarios_dir, exemplars_dir):
        if root.exists():
            files.extend(sorted(root.rglob('*.json')))
    return files


def _validate_scenarios(scenarios: list[dict], source_path: Path) -> None:
    """Basic structural validation against schema expectations."""
    required_keys = {
        'id',
        'type',
        'source_file',
        'section',
        'tags',
        'severity',
        'explanation',
        'bdd',
        'verbatim_rule',
        'examples',
        'mcp_tool_hint',
    }
    bdd_required = {'given', 'when', 'then'}

    for i, s in enumerate(scenarios):
        missing = required_keys - set(s.keys())
        if missing:
            raise ValueError(
                f'{source_path}[{i}] (id={s.get("id", "?")}): missing keys: {missing}'
            )
        bdd_missing = bdd_required - set(s.get('bdd', {}).keys())
        if bdd_missing:
            raise ValueError(
                f'{source_path}[{i}] (id={s.get("id", "?")}): bdd missing keys:'
                f' {bdd_missing}'
            )


def _build_embedding_text(scenario: dict) -> str:
    """Build a single text string from a scenario for embedding.

    Concatenates the fields most relevant for semantic retrieval:
    section, tags, BDD clauses, verbatim rule, and explanation.
    """
    parts = [
        f'Section: {scenario["section"]}',
        f'Tags: {", ".join(scenario["tags"])}',
        f'GIVEN {scenario["bdd"]["given"]}',
        f'WHEN {scenario["bdd"]["when"]}',
        f'THEN {scenario["bdd"]["then"]}',
    ]
    for clause in scenario['bdd'].get('and', []):
        parts.append(f'AND {clause}')
    for clause in scenario['bdd'].get('but', []):
        parts.append(f'BUT {clause}')
    parts.append(f'Rule: {scenario["verbatim_rule"]}')
    parts.append(f'Explanation: {scenario["explanation"]}')
    return '\n'.join(parts)


def _serialize_f32_vec(vec: list[float]) -> bytes:
    """Serialize a list of floats to a compact binary blob for sqlite-vec."""
    return struct.pack(f'{len(vec)}f', *vec)


# ---------------------------------------------------------------------------
# Embedding client
# ---------------------------------------------------------------------------


class EmbeddingClient:
    """Local embedding via fastembed (ONNX Runtime). No API key, no network."""

    def __init__(self) -> None:
        from fastembed import TextEmbedding  # type: ignore[import-untyped]

        self._model = TextEmbedding(model_name=_EMBEDDING_MODEL)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one float vector per text."""
        embeddings = self._model.embed(texts)
        return [vec.tolist() for vec in embeddings]


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


def _init_db(db_path: Path) -> sqlite3.Connection:
    """Create or open the DB with WAL mode and sqlite-vec loaded."""
    import sqlite_vec  # type: ignore[import-untyped]

    conn = sqlite3.connect(str(db_path))
    if not hasattr(conn, 'enable_load_extension'):
        conn.close()
        raise RuntimeError(
            'Python SQLite extension loading is unavailable. Run this command '
            'through the release-pinned uvx package.'
        )
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute(_CREATE_SCENARIOS_TABLE)
    # vec0 virtual tables do not support IF NOT EXISTS — check first.
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND "
        "name='scenario_embeddings'"
    ).fetchone()
    if not existing:
        conn.execute(_CREATE_EMBEDDINGS_TABLE)
    conn.commit()
    return conn


def _upsert_scenario(
    conn: sqlite3.Connection, scenario: dict, embedding: list[float]
) -> None:
    """Insert or replace a scenario and its embedding."""
    conn.execute(
        """INSERT OR REPLACE INTO scenarios
           (id, type, source_file, section, tags, severity,
            bdd_given, bdd_when, bdd_then, verbatim_rule, json_blob)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            scenario['id'],
            scenario['type'],
            scenario['source_file'],
            scenario['section'],
            json.dumps(scenario['tags']),
            scenario['severity'],
            scenario['bdd']['given'],
            scenario['bdd']['when'],
            scenario['bdd']['then'],
            scenario['verbatim_rule'],
            json.dumps(scenario),
        ),
    )
    conn.execute(
        'DELETE FROM scenario_embeddings WHERE id = ?',
        (scenario['id'],),
    )
    conn.execute(
        'INSERT INTO scenario_embeddings (id, embedding) VALUES (?, ?)',
        (scenario['id'], _serialize_f32_vec(embedding)),
    )


def _get_all_scenario_ids(conn: sqlite3.Connection) -> set[str]:
    """Return all scenario IDs currently in the DB."""
    rows = conn.execute('SELECT id FROM scenarios').fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------


def sync(*, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Run the sync pipeline. Returns a summary dict."""
    t0 = time.monotonic()
    last_sync = _load_last_sync()
    prev_hashes = last_sync.get('file_hashes', {})

    # Detect embedding model change — force full rebuild.
    if (
        last_sync.get('embedding_model')
        and last_sync['embedding_model'] != _EMBEDDING_MODEL
    ):
        print(
            f'[sync_kb] Embedding model changed: {last_sync["embedding_model"]} ->'
            f' {_EMBEDDING_MODEL}'
        )
        print('[sync_kb] Forcing full rebuild.')
        force = True

    if (
        last_sync.get('embedding_dimensions')
        and last_sync['embedding_dimensions'] != _EMBEDDING_DIMENSIONS
    ):
        print(
            f'[sync_kb] Embedding dimensions changed: '
            f'{last_sync["embedding_dimensions"]} -> {_EMBEDDING_DIMENSIONS}'
        )
        print('[sync_kb] Forcing full rebuild.')
        force = True

    # Discover and hash scenario files.
    scenario_files = _discover_scenario_files()
    if not scenario_files:
        print('[sync_kb] No scenario files found. Nothing to sync.')
        return {'status': 'empty', 'scenario_count': 0}

    current_hashes: dict[str, str] = {}
    changed_files: list[Path] = []
    unchanged_files: list[Path] = []

    for f in scenario_files:
        rel = str(f.relative_to(get_config().home()))
        h = _sha256(f)
        current_hashes[rel] = h
        if force or prev_hashes.get(rel) != h:
            changed_files.append(f)
        else:
            unchanged_files.append(f)

    # Detect deleted files.
    current_rels = set(current_hashes.keys())
    prev_rels = set(prev_hashes.keys())
    deleted_rels = prev_rels - current_rels

    print(f'[sync_kb] Discovered {len(scenario_files)} scenario files.')
    print(
        f'[sync_kb] Changed: {len(changed_files)}, Unchanged: {len(unchanged_files)}, '
        f'Deleted: {len(deleted_rels)}'
    )

    if not changed_files and not deleted_rels:
        print('[sync_kb] Nothing to sync — all files up to date.')
        # Still write last_sync to update timestamp.
        if not dry_run:
            _save_last_sync(
                {
                    'synced_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'embedding_model': _EMBEDDING_MODEL,
                    'embedding_dimensions': _EMBEDDING_DIMENSIONS,
                    'embedding_phase': _EMBEDDING_PHASE,
                    'file_hashes': current_hashes,
                    'scenario_count': last_sync.get('scenario_count', 0),
                    'exemplar_count': last_sync.get('exemplar_count', 0),
                    'db_version': '1.0.0',
                }
            )
        return {
            'status': 'up_to_date',
            'scenario_count': last_sync.get('scenario_count', 0),
        }

    if dry_run:
        print('[sync_kb] DRY RUN — no changes written.')
        for f in changed_files:
            scenarios = json.loads(f.read_text(encoding='utf-8'))
            print(
                f'  Would embed {len(scenarios)} scenarios from '
                f'{f.relative_to(get_config().home())}'
            )
        for rel in sorted(deleted_rels):
            print(f'  Would remove stale scenarios from deleted file: {rel}')
        return {'status': 'dry_run', 'changed_files': len(changed_files)}

    # --- Actual sync ---

    # Initialise embedding client.
    embedder = EmbeddingClient()

    # Initialise DB (creates if missing).
    tmp_db_path: Path | None = None
    db_path = get_config().rules_db_path()
    if force:
        tmp_db_path = db_path.with_suffix('.tmp')
        if tmp_db_path.exists():
            tmp_db_path.unlink()
        conn = _init_db(tmp_db_path)
    else:
        conn = _init_db(db_path)

    # If force, we already deleted the DB — all files are "changed".
    if force:
        changed_files = scenario_files

    # Collect all scenarios from changed files.
    all_scenarios: list[dict] = []
    all_texts: list[str] = []
    scenario_count = 0
    exemplar_count = 0
    errors: list[str] = []

    for f in changed_files:
        try:
            scenarios = json.loads(f.read_text(encoding='utf-8'))
            _validate_scenarios(scenarios, f)
        except (json.JSONDecodeError, ValueError) as e:
            errors.append(f'{f.relative_to(get_config().home())}: {e}')
            continue

        for s in scenarios:
            all_scenarios.append(s)
            all_texts.append(_build_embedding_text(s))
            if s.get('type') == 'exemplar':
                exemplar_count += 1
            else:
                scenario_count += 1

    if errors:
        print(f'[sync_kb] WARNING: {len(errors)} file(s) failed validation:')
        for e in errors:
            print(f'  {e}')

    # Count scenarios from unchanged files (already in DB).
    for f in unchanged_files:
        try:
            scenarios = json.loads(f.read_text(encoding='utf-8'))
            for s in scenarios:
                if s.get('type') == 'exemplar':
                    exemplar_count += 1
                else:
                    scenario_count += 1
        except (json.JSONDecodeError, ValueError):
            pass

    # Remove stale scenarios from deleted files or in-file ID renames.
    # Collect every ID that currently exists across all remaining scenario
    # files, then delete any DB row whose ID is absent. This runs before
    # the embed step so that deletion-only syncs (no changed files) still
    # clean up the DB, and also catches IDs renamed inside a changed file.
    if (deleted_rels or changed_files) and not force:
        live_ids: set[str] = set()
        for f in scenario_files:
            try:
                for s in json.loads(f.read_text(encoding='utf-8')):
                    if s.get('id'):
                        live_ids.add(s['id'])
            except (json.JSONDecodeError, ValueError):
                pass
        db_ids = _get_all_scenario_ids(conn)
        stale_ids = db_ids - live_ids
        if stale_ids:
            placeholders = ','.join('?' * len(stale_ids))
            conn.execute(
                f'DELETE FROM scenario_embeddings WHERE id IN ({placeholders})',
                list(stale_ids),
            )
            conn.execute(
                f'DELETE FROM scenarios WHERE id IN ({placeholders})', list(stale_ids)
            )
            conn.commit()
            print(
                f'[sync_kb] Removed {len(stale_ids)} stale scenario(s) '
                f'({len(deleted_rels)} deleted file(s), renamed IDs cleaned up).'
            )

    if not all_scenarios:
        conn.close()
        if deleted_rels:
            # Deletion-only sync: update last_sync.json to reflect new state.
            sync_data = {
                'synced_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'embedding_model': _EMBEDDING_MODEL,
                'embedding_dimensions': _EMBEDDING_DIMENSIONS,
                'embedding_phase': _EMBEDDING_PHASE,
                'file_hashes': current_hashes,
                'scenario_count': scenario_count,
                'exemplar_count': exemplar_count,
                'db_version': '1.0.0',
            }
            _save_last_sync(sync_data)
            return {'status': 'ok', 'scenario_count': scenario_count}
        print('[sync_kb] No valid scenarios to embed after validation.')
        return {'status': 'error', 'errors': errors}

    # Embed all texts.
    print(
        f'[sync_kb] Embedding {len(all_texts)} scenarios via {_EMBEDDING_MODEL} ('
        f'{_EMBEDDING_DIMENSIONS}d)...'
    )
    t_embed = time.monotonic()
    embeddings = embedder.embed_batch(all_texts)
    embed_duration = time.monotonic() - t_embed
    print(f'[sync_kb] Embedding complete in {embed_duration:.1f}s.')

    # Upsert into DB.
    for scenario, embedding in zip(all_scenarios, embeddings):
        _upsert_scenario(conn, scenario, embedding)

    conn.commit()
    conn.close()

    if tmp_db_path is not None:
        os.replace(str(tmp_db_path), str(db_path))
        # Clean up both temp and target WAL/SHM sidecars after the swap.
        for suffix in ('-wal', '-shm'):
            wal_path = Path(f'{tmp_db_path}{suffix}')
            if wal_path.exists():
                wal_path.unlink()
        for suffix in ('-wal', '-shm'):
            target_sidecar = Path(f'{db_path}{suffix}')
            if target_sidecar.exists():
                target_sidecar.unlink()

    # Write last_sync.json.
    sync_data = {
        'synced_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'embedding_model': _EMBEDDING_MODEL,
        'embedding_dimensions': _EMBEDDING_DIMENSIONS,
        'embedding_phase': _EMBEDDING_PHASE,
        'file_hashes': current_hashes,
        'scenario_count': scenario_count,
        'exemplar_count': exemplar_count,
        'db_version': '1.0.0',
    }
    _save_last_sync(sync_data)

    total_duration = time.monotonic() - t0
    print('[sync_kb] Sync complete.')
    print(f'  Scenarios embedded: {len(all_scenarios)}')
    print(f'  Total scenarios in corpus: {scenario_count}')
    print(f'  Total exemplars in corpus: {exemplar_count}')
    print(f'  Validation errors: {len(errors)}')
    print(f'  Embedding time: {embed_duration:.1f}s')
    print(f'  Total time: {total_duration:.1f}s')

    return {
        'status': 'ok',
        'embedded': len(all_scenarios),
        'scenario_count': scenario_count,
        'exemplar_count': exemplar_count,
        'errors': len(errors),
        'embed_seconds': round(embed_duration, 1),
        'total_seconds': round(total_duration, 1),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    from agent_smith.config import configure_cli_environment, ensure_sqlite_cli_runtime

    configure_cli_environment()
    ensure_sqlite_cli_runtime('agent-smith-kb-sync')
    parser = argparse.ArgumentParser(
        description='Sync Agent Smith knowledge base scenarios to rules.db.',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Delete and rebuild rules.db from scratch.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would change without writing anything.',
    )
    args = parser.parse_args()

    result = sync(force=args.force, dry_run=args.dry_run)

    if result.get('status') == 'error' or result.get('errors', 0) > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
