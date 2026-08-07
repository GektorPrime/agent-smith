"""Visualize the Agent Smith knowledge base in 3D.

Launches two views:
  1. Plotly 3D scatter — pre-computed UMAP (n_components=3), fully offline
     self-contained HTML, opens in the default browser.
  2. Renumics Spotlight — 2D similarity map + inspector/table panel for
     full metadata inspection on point click.

Run via the console script:
    agent-smith-kb-visualize

Requires the viz extra:
    pip install 'agent_smith[viz]'
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
import sys
import tempfile
import webbrowser
from pathlib import Path

from agent_smith.config import get_config

_EMBEDDING_DIM = 384

def _require_viz_deps() -> None:
    try:
        import numpy  # noqa: F401
        import pandas  # type: ignore[import-not-found]  # noqa: F401
        import plotly.graph_objects  # type: ignore[import-not-found]  # noqa: F401
        import sqlite_vec  # noqa: F401
        import umap  # type: ignore[import-not-found]  # noqa: F401
        from renumics import spotlight  # type: ignore[import-not-found]  # noqa: F401
    except ModuleNotFoundError:
        from agent_smith.config import reexec_cli_runtime

        reexec_cli_runtime(
            'agent-smith-kb-visualize',
            extra='viz',
            module='agent_smith.kdb.tools.visualize_kb',
        )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _open_db() -> sqlite3.Connection:
    import sqlite_vec  # type: ignore[import-untyped]

    db_path = get_config().rules_db_path()
    if not db_path.exists():
        print(
            f'[visualize_kb] ERROR: rules.db not found at {db_path}\n'
            '               Run `agent-smith-kb-sync` first.',
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
    rows = conn.execute('SELECT id, embedding FROM scenario_embeddings').fetchall()
    return {row[0]: list(struct.unpack(f'{_EMBEDDING_DIM}f', row[1])) for row in rows}


def _output_dir() -> Path:
    out = get_config().home() / 'knowledge_base' / 'tmp'
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# DataFrame builder
# ---------------------------------------------------------------------------


def _derive_test_type(tags_json: str) -> str:
    """Extract test_type from a JSON-serialised tags array.

    Returns the value after 'test_type:' prefix if present, else 'core'.
    """
    try:
        tags: list[str] = json.loads(tags_json)
    except (json.JSONDecodeError, TypeError):
        return 'core'
    for tag in tags:
        if tag.startswith('test_type:'):
            return tag[len('test_type:') :]
    return 'core'


def _build_dataframe(scenarios: list[dict], vectors: dict[str, list[float]]):
    """Build a pandas DataFrame with metadata + embedding column.

    The `embedding` column carries the raw 384-dim vectors for the similarity
    map. All other columns are plain strings for readable table/inspector
    display. Tags are flattened to a comma-separated string so Spotlight
    renders them without errors.
    """
    import numpy as np
    import pandas as pd  # type: ignore[import-not-found]

    rows = []

    for s in scenarios:
        sid = s['id']
        if sid not in vectors:
            continue
        try:
            tags_list: list[str] = json.loads(s['tags'])
        except (json.JSONDecodeError, TypeError):
            tags_list = []
        try:
            blob: dict = json.loads(s['json_blob'])
        except (json.JSONDecodeError, TypeError):
            blob = {}
        bdd = blob.get('bdd', {})
        examples = blob.get('examples', {})
        rows.append(
            {
                'id': sid,
                'type': s['type'],
                'source_file': s['source_file'],
                'section': s['section'],
                'tags': ', '.join(tags_list),
                'test_type': _derive_test_type(s['tags']),
                'severity': s['severity'],
                'bdd_given': s['bdd_given'],
                'bdd_when': s['bdd_when'],
                'bdd_then': s['bdd_then'],
                'bdd_and': '\n'.join(bdd.get('and', [])),
                'bdd_but': '\n'.join(bdd.get('but', [])),
                'verbatim_rule': s['verbatim_rule'],
                'explanation': blob.get('explanation', ''),
                'example_correct': examples.get('correct') or '',
                'example_incorrect': examples.get('incorrect') or '',
                'mcp_tool_hint': blob.get('mcp_tool_hint') or '',
                'embedding': np.array(vectors[sid], dtype=np.float32),
            }
        )

    df = pd.DataFrame(rows)
    return df[list(_TABLE_COLUMNS) + ['embedding']]


# ---------------------------------------------------------------------------
# UMAP 3D + Plotly
# ---------------------------------------------------------------------------

# Fixed colour palette — one colour per test_type value.
# Using Plotly's qualitative Safe palette (colourblind-friendly).
_PLOTLY_COLOURS = [
    '#88CCEE',
    '#CC6677',
    '#DDCC77',
    '#117733',
    '#332288',
    '#AA4499',
    '#44AA99',
    '#999933',
    '#882255',
    '#661100',
]


def _compute_umap_3d(df):
    """Add umap_x, umap_y, umap_z columns computed from the embedding column."""
    import numpy as np
    import umap  # type: ignore[import-not-found]

    matrix = np.stack(df['embedding'].to_numpy()).astype(np.float32)
    reducer = umap.UMAP(n_components=3, random_state=42)
    coords = reducer.fit_transform(matrix)
    df = df.copy()
    df['umap_x'] = coords[:, 0].astype(float)
    df['umap_y'] = coords[:, 1].astype(float)
    df['umap_z'] = coords[:, 2].astype(float)
    return df


def _write_plotly_3d(df) -> str:
    """Write a self-contained offline 3D scatter HTML and open it in the browser.

    Uses Plotly.newPlot + Plotly.relayout for camera rotation (no frames/animate).
    Plain HTML buttons (bottom-left) drive Play/Stop via setInterval.
    Plotly JS is bundled inline — fully offline.

    Returns the random suffix used in the filename so the caller can attach a
    matching coords sidecar (``agent_smith_kb_coords_<suffix>.json``).
    """
    import json as _json

    import plotly.graph_objects as go  # type: ignore[import-not-found]

    test_types = sorted(df['test_type'].unique())
    colour_map = {
        tt: _PLOTLY_COLOURS[i % len(_PLOTLY_COLOURS)] for i, tt in enumerate(test_types)
    }

    traces = []
    for tt in test_types:
        sub = df[df['test_type'] == tt]
        hover = (
            '<b>%{customdata[0]}</b><br>'
            'severity: %{customdata[1]}<br>'
            'tags: %{customdata[3]}<br>'
            'rule: %{customdata[2]}<extra></extra>'
        )
        traces.append(
            go.Scatter3d(
                x=sub['umap_x'],
                y=sub['umap_y'],
                z=sub['umap_z'],
                mode='markers',
                name=tt,
                marker=dict(size=5, color=colour_map[tt], opacity=0.85),
                customdata=list(
                    zip(
                        sub['id'],
                        sub['severity'],
                        sub['verbatim_rule'].str[:80],
                        sub['tags'],
                    )
                ),
                hovertemplate=hover,
            )
        )

    camera_r = 2.8  # zoomed out — full galaxy visible without clipping
    camera_z = 0.4
    n_steps = 360  # one step per degree — full circle at 100ms/step ≈ 36s

    # Pre-compute all camera eye positions — passed to JS as a JSON array.
    cameras = [
        {
            'x': camera_r * math.cos(i * 2 * math.pi / n_steps),
            'y': camera_r * math.sin(i * 2 * math.pi / n_steps),
            'z': camera_z,
        }
        for i in range(n_steps)
    ]

    _axis = dict(
        backgroundcolor='#ffffff',
        gridcolor='#cccccc',
        showbackground=True,
        zerolinecolor='#999999',
    )
    fig = go.Figure(
        data=traces,
        layout=go.Layout(
            paper_bgcolor='#ffffff',
            plot_bgcolor='#ffffff',
            scene=dict(
                xaxis=dict(title='UMAP 1', **_axis),
                yaxis=dict(title='UMAP 2', **_axis),
                zaxis=dict(title='UMAP 3', **_axis),
                bgcolor='#ffffff',
                camera=dict(eye=cameras[0]),
            ),
            showlegend=True,
            legend=dict(title='test_type', y=0.5, yanchor='middle'),
            margin=dict(l=0, r=0, t=0, b=0),
        ),
    )

    # Use the supported Plotly API to obtain the bundled offline JS directly,
    # avoiding temp-file I/O and the "largest <script>" heuristic.
    import plotly.offline as _po  # type: ignore[import-not-found]

    plotlyjs_block = f'<script>{_po.get_plotlyjs()}</script>'

    fig_json = _json.loads(fig.to_json())
    plot_data = _json.dumps(fig_json['data'])
    plot_layout = _json.dumps(fig_json['layout'])

    cameras_js = _json.dumps(cameras)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ margin: 0; background: #ffffff; }}
  #gd {{ width: 100vw; height: 100vh; }}
  #kb-bar {{
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: 44px;
    background: #f8f8f8;
    border-top: 1px solid #ddd;
    display: flex;
    align-items: center;
    padding: 0 16px;
    gap: 8px;
    z-index: 999;
    font-family: sans-serif;
    font-size: 13px;
    color: #555;
  }}
  .kb-btn {{
    padding: 5px 16px;
    font-size: 13px;
    font-family: sans-serif;
    background: #f0f0f0;
    color: #333;
    border: 1px solid #bbb;
    border-radius: 4px;
    cursor: pointer;
  }}
  .kb-btn:hover {{ background: #ddd; }}
  #kb-title {{
    margin-left: auto;
    margin-right: 16px;
    font-size: 12px;
    color: #888;
  }}
</style>
</head>
<body>
{plotlyjs_block}
<div id="gd"></div>
<div id="kb-bar">
  <button class="kb-btn" id="btn-play">Play</button>
  <button class="kb-btn" id="btn-stop">Stop</button>
  <span id="kb-title">Agent Smith Knowledge Base</span>
</div>
<script>
(function() {{
  var cameras = {cameras_js};
  var N = {n_steps};
  var frame = 0, timer = null;

  function step() {{
    Plotly.relayout('gd', {{'scene.camera.eye': cameras[frame]}});
    frame = (frame + 1) % N;
  }}
  function play() {{ if (!timer) timer = setInterval(step, 100); }}
  function stop() {{ clearInterval(timer); timer = null; }}

  document.addEventListener('DOMContentLoaded', function() {{
    document.getElementById('btn-play').addEventListener('click', play);
    document.getElementById('btn-stop').addEventListener('click', stop);
  }});

  Plotly.newPlot('gd', {plot_data}, {plot_layout}, {{responsive: true}})
    .then(function() {{ play(); }});
}})();
</script>
</body>
</html>"""

    output_dir = _output_dir()
    fd, html_path = tempfile.mkstemp(
        suffix='.html',
        prefix='agent_smith_kb_3d_',
        dir=output_dir,
    )
    os.close(fd)
    with open(html_path, 'w') as f:
        f.write(html)
    webbrowser.open(f'file://{html_path}')

    # Extract the random suffix that mkstemp embedded in the filename so the
    # caller can create a matching coords sidecar with the same suffix.
    # Filename pattern: agent_smith_kb_3d_<suffix>.html
    stem = Path(html_path).stem  # e.g. "agent_smith_kb_3d_g2it1i8r"
    suffix = stem[len('agent_smith_kb_3d_') :]  # e.g. "g2it1i8r"
    return suffix


# ---------------------------------------------------------------------------
# Coords sidecar
# ---------------------------------------------------------------------------


def _write_coords_sidecar(df, suffix: str) -> Path:
    """Write UMAP coordinates + scenario metadata as a JSON sidecar.

    Output file: ``<AGENT_SMITH_HOME>/knowledge_base/tmp/agent_smith_kb_coords_<suffix>.json``

    The suffix matches the 3D HTML file (``agent_smith_kb_3d_<suffix>.html``)
    so the two artefacts from the same run are trivially matchable.

    Schema (list of objects, one per scenario)::

        [
          {
            "id":        "conv-assert-001",
            "trace":     "core",               // UMAP colour group (test_type)
            "x":         8.763,
            "y":         8.351,
            "z":         3.451,
            "severity":  "hard",
            "tags":      "core, assertion",
            "then":      "the actual value MUST be on the left..."
          },
          ...
        ]
    """
    records = []
    for _, row in df.iterrows():
        records.append(
            {
                'id': row['id'],
                'trace': row['test_type'],
                'x': round(float(row['umap_x']), 6),
                'y': round(float(row['umap_y']), 6),
                'z': round(float(row['umap_z']), 6),
                'severity': row['severity'],
                'tags': row['tags'],
                'then': row['bdd_then'],
            }
        )

    coords_path = _output_dir() / f'agent_smith_kb_coords_{suffix}.json'
    with open(coords_path, 'w') as f:
        json.dump(records, f, indent=2)
    return coords_path


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


_TABLE_COLUMNS = [
    'id',
    'type',
    'test_type',
    'source_file',
    'section',
    'tags',
    'severity',
    'explanation',
    'bdd_given',
    'bdd_when',
    'bdd_then',
    'bdd_and',
    'bdd_but',
    'verbatim_rule',
    'example_correct',
    'example_incorrect',
    'mcp_tool_hint',
]


def _build_layout() -> object:
    """Two-panel layout: similarity map on the left, inspector on the right."""
    from renumics.spotlight.layout import (  # type: ignore[import-not-found]
        inspector,
        similaritymap,
        split,
        tab,
        table,
    )
    from renumics.spotlight.layout import lenses as L  # type: ignore[import-not-found]

    sim_map = similaritymap(
        columns=['embedding'],
        reduction_method='umap',
        color_by_column='test_type',
    )
    detail = inspector(
        num_columns=1,
        lenses=[
            L.scalar('id'),
            L.scalar('test_type'),
            L.scalar('severity'),
            L.scalar('type'),
            L.scalar('section'),
            L.scalar('tags'),
            L.scalar('source_file'),
            L.scalar('mcp_tool_hint'),
            L.text('verbatim_rule'),
            L.markdown('explanation'),
            L.text('bdd_given'),
            L.text('bdd_when'),
            L.text('bdd_then'),
            L.markdown('bdd_and'),
            L.markdown('bdd_but'),
            L.text('example_correct'),
            L.text('example_incorrect'),
        ],
    )
    return split(
        tab(sim_map),
        tab(detail, table(visible_columns=_TABLE_COLUMNS)),
        weight=0.6,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    from agent_smith.config import configure_cli_environment, ensure_sqlite_cli_runtime

    configure_cli_environment()

    parser = argparse.ArgumentParser(description='Visualize the Agent Smith KB.')
    parser.add_argument(
        '--no-spotlight',
        action='store_true',
        help='Skip the Spotlight interactive view '
        '(write HTML + coords sidecar and exit).',
    )
    args = parser.parse_args()

    _require_viz_deps()
    ensure_sqlite_cli_runtime('agent-smith-kb-visualize')

    conn = _open_db()
    scenarios = _load_scenarios(conn)
    vectors = _load_vectors(conn)
    conn.close()

    print(f'[visualize_kb] rules.db found: {len(scenarios)} scenarios')
    print('[visualize_kb] Building DataFrame...')

    df = _build_dataframe(scenarios, vectors)

    print('[visualize_kb] Computing UMAP 3D projection...')
    df = _compute_umap_3d(df)

    print('[visualize_kb] Opening 3D scatter in browser...')
    suffix = _write_plotly_3d(df)

    coords_path = _write_coords_sidecar(df, suffix)
    print(f'[visualize_kb] Coords sidecar written: {coords_path}')

    if args.no_spotlight:
        print('[visualize_kb] --no-spotlight set, skipping Spotlight. Done.')
        return

    print('[visualize_kb] Launching Spotlight...')
    from renumics import spotlight  # type: ignore[import-not-found]
    from renumics.spotlight import dtypes  # type: ignore[import-not-found]

    spotlight.show(
        df,
        dtype={
            'embedding': dtypes.Embedding,
            'test_type': dtypes.Category,
            'severity': dtypes.Category,
            'type': dtypes.Category,
        },
        layout=_build_layout(),
        wait=True,
    )


if __name__ == '__main__':
    main()
