"""Regression guard: the ``viz`` extra must pin a pandas release that survives
Python-3.14 pickle round-trips of ``string[python]`` (broken on <2.3.3).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / 'pyproject.toml'


def _pandas_pin_in_viz() -> str:
    """Return the pinned pandas version from the ``viz`` optional dependency."""
    with PYPROJECT.open('rb') as f:
        data = tomllib.load(f)
    deps = data['project']['optional-dependencies']['viz']
    for spec in deps:
        if spec.startswith('pandas=='):
            return spec.split('==', 1)[1]
    raise AssertionError(
        f'pandas==x.y.z not found in [project.optional-dependencies].viz in {PYPROJECT}'
    )


class TestVizPandasPin:
    """Protect the ``viz`` extra against a regression to a blocked pandas."""

    def test_pandas_is_pinned_to_2_3_3(self) -> None:
        pin = _pandas_pin_in_viz()
        assert pin == '2.3.3', (
            f'expected pandas==2.3.3 (Python-3.14 pickle-safe), got pandas=={pin}'
        )
