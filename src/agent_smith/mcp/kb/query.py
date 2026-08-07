"""Knowledge base query engine for Agent Smith.

Lazily loads the fastembed model and sqlite-vec DB connection on first use.
Provides query_rules() for semantic retrieval, kb_ready() for lightweight
database validation, and db_available() for full query-engine readiness.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import sys

from agent_smith.config import get_config

# ---------------------------------------------------------------------------
# Embedding config
# ---------------------------------------------------------------------------

_EMBEDDING_MODEL_NAME = 'BAAI/bge-small-en-v1.5'
_EMBEDDING_DIMENSIONS = 384

# ---------------------------------------------------------------------------
# Startup loading
# ---------------------------------------------------------------------------


def _load_embedding_model():
    """Load the fastembed model (~133MB download on first-ever run, cached)."""
    try:
        from fastembed import TextEmbedding  # type: ignore[import-untyped]

        return TextEmbedding(model_name=_EMBEDDING_MODEL_NAME)
    except Exception as e:
        print(f'[kb] load error ({type(e).__name__}): {e}', file=sys.stderr)
        return None


def _load_db_conn() -> sqlite3.Connection | None:
    """Open a read-only connection to rules.db with sqlite-vec loaded."""
    db_path = get_config().rules_db_path()
    if not db_path.exists():
        return None
    try:
        import sqlite_vec  # type: ignore[import-untyped]

        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return conn
    except Exception as e:
        print(f'[kb] load error ({type(e).__name__}): {e}', file=sys.stderr)
        return None


_embedding_model = None
_db_conn: sqlite3.Connection | None = None
_loaded = False


def sqlite_extensions_available() -> bool:
    """Return whether this Python build permits SQLite extension loading."""
    conn = sqlite3.connect(':memory:')
    try:
        return hasattr(conn, 'enable_load_extension')
    finally:
        conn.close()


def _ensure_loaded() -> None:
    global _embedding_model, _db_conn, _loaded
    if _loaded:
        return
    _embedding_model = _load_embedding_model()
    _db_conn = _load_db_conn()
    _loaded = True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reset() -> None:
    """Close cached resources so the next query loads current state."""
    global _embedding_model, _db_conn, _loaded
    if _db_conn is not None:
        try:
            _db_conn.close()
        except Exception as e:
            print(f'[kb] reset error ({type(e).__name__}): {e}', file=sys.stderr)
    _embedding_model = None
    _db_conn = None
    _loaded = False


def load() -> None:
    """Force-(re)load the embedding model and DB connection.

    Useful after sync_kb.py rebuilds rules.db during a running session.
    """
    reset()
    _ensure_loaded()


def _connection_ready(conn: sqlite3.Connection | None) -> bool:
    if conn is None:
        return False
    try:
        quick_check = conn.execute('PRAGMA quick_check').fetchone()
        if quick_check != ('ok',):
            return False
        embedding_schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'scenario_embeddings'"
        ).fetchone()
        if (
            embedding_schema is None
            or not isinstance(embedding_schema[0], str)
            or f'float[{_EMBEDDING_DIMENSIONS}]'
            not in embedding_schema[0].lower().replace(' ', '')
        ):
            return False
        row = conn.execute(
            """
            SELECT 1
            FROM scenario_embeddings e
            JOIN scenarios s ON s.id = e.id
            LIMIT 1
            """
        ).fetchone()
        return row is not None
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
        print(f'[kb] readiness error ({type(e).__name__}): {e}', file=sys.stderr)
        return False


def kb_ready() -> bool:
    """Return True when rules.db has a queryable schema and indexed content.

    This intentionally does not load the embedding model. Session
    initialization can therefore choose the protocol without paying model
    startup cost or requiring a model download merely to inspect the DB.
    """
    conn = _load_db_conn()
    if conn is None:
        return False
    try:
        return _connection_ready(conn)
    finally:
        conn.close()


def db_available() -> bool:
    """Return True if both the DB and embedding model are operational."""
    _ensure_loaded()
    return _embedding_model is not None and _connection_ready(_db_conn)


def query_rules(
    query: str,
    tags: list[str] | None = None,
    k: int = 5,
) -> str:
    """Semantic search against rules.db.

    Args:
        query: Natural-language description of the operation.
        tags:  Optional tag pre-filter (matches ANY provided tag).
        k:     Number of results (1–20).

    Returns:
        JSON string — array of scenario objects on success, or an error
        object when the DB is unavailable.
    """
    _ensure_loaded()
    if _embedding_model is None or not _connection_ready(_db_conn):
        return json.dumps(
            {
                'error': 'rules.db is unavailable',
                'db_available': False,
                'hint': 'Run agent-smith-kb-sync --force to build the knowledge base.',
            }
        )

    assert _db_conn is not None
    k = max(1, min(k, 20))

    # Embed the query.
    vecs = list(_embedding_model.embed([query]))
    query_vec = struct.pack(f'{len(vecs[0])}f', *vecs[0].tolist())

    # If tags are provided, over-fetch then filter client-side.
    # Multiplier of 10 ensures tag-filtered results are found even when
    # the query is generic and matching scenarios are spread across the top-N.
    fetch_k = min(k * 10, 100) if tags else k

    rows = _db_conn.execute(
        """
        SELECT e.id, e.distance, s.json_blob, s.tags
        FROM scenario_embeddings e
        JOIN scenarios s ON s.id = e.id
        WHERE e.embedding MATCH ? AND k = ?
        ORDER BY e.distance
        """,
        (query_vec, fetch_k),
    ).fetchall()

    # Apply tag filter if provided.
    if tags:
        filtered = []
        for row in rows:
            scenario_tags = json.loads(row[3])
            if any(t in scenario_tags for t in tags):
                filtered.append(row)
        rows = filtered[:k]
    else:
        rows = rows[:k]

    # Build result.
    results = []
    for row in rows:
        scenario = json.loads(row[2])
        scenario['_distance'] = round(row[1], 4)
        results.append(scenario)

    return json.dumps(results, indent=2)
