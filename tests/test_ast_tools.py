from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable

import pytest

from agent_smith.ast_tools.search import (
    agent_smith_ast_find_definitions,
    agent_smith_ast_search,
)
from agent_smith.ast_tools.structure import (
    agent_smith_ast_analyze_structure,
    agent_smith_ast_class_outline,
    agent_smith_ast_list_imports,
)


FIXTURES_DIR = Path(__file__).parent / 'fixtures'


def run(coro: Awaitable[str]) -> str:
    return asyncio.run(coro)


def test_analyze_structure_python_contains_class_method_and_import() -> None:
    payload = run(agent_smith_ast_analyze_structure(str(FIXTURES_DIR / 'sample.py')))
    data = json.loads(payload)

    assert isinstance(data, list)
    assert any(item['symbol_type'] == 'class' and item['name'] == 'CircleService' for item in data)
    assert any(item['symbol_type'] == 'method' for item in data)
    assert any(item['symbol_type'] == 'import' for item in data)


@pytest.mark.parametrize('name', ['sample.ts', 'sample.php', 'sample.go'])
def test_analyze_structure_other_languages_returns_non_empty_list(name: str) -> None:
    payload = run(agent_smith_ast_analyze_structure(str(FIXTURES_DIR / name)))
    data = json.loads(payload)
    assert isinstance(data, list)
    assert data


def test_analyze_structure_non_existent_path_raises_value_error() -> None:
    with pytest.raises(ValueError):
        run(agent_smith_ast_analyze_structure(str(FIXTURES_DIR / 'missing.py')))


def test_class_outline_returns_circle_service_with_members() -> None:
    payload = run(agent_smith_ast_class_outline(str(FIXTURES_DIR / 'sample.py')))
    data = json.loads(payload)

    circle = next(item for item in data if item['name'] == 'CircleService')
    assert circle['members']


def test_class_outline_filters_by_class_name() -> None:
    payload = run(agent_smith_ast_class_outline(
        str(FIXTURES_DIR / 'sample.py'), class_name='CircleService'
    ))
    data = json.loads(payload)
    assert len(data) == 1
    assert data[0]['name'] == 'CircleService'


def test_class_outline_nonexistent_class_returns_empty() -> None:
    payload = run(agent_smith_ast_class_outline(
        str(FIXTURES_DIR / 'sample.py'), class_name='Nonexistent'
    ))
    data = json.loads(payload)
    assert data == []


def test_list_imports_python_and_typescript() -> None:
    py_payload = run(agent_smith_ast_list_imports(str(FIXTURES_DIR / 'sample.py')))
    py_imports = json.loads(py_payload)
    statements = [item['statement'] for item in py_imports]
    assert len(py_imports) >= 2
    assert any('import math' in statement for statement in statements)

    ts_payload = run(agent_smith_ast_list_imports(str(FIXTURES_DIR / 'sample.ts')))
    ts_imports = json.loads(ts_payload)
    assert ts_imports


def test_ast_search_python_and_go_and_metavariable_keys_are_declared() -> None:
    py_pattern = 'def $NAME($$$ARGS)'
    py_payload = run(agent_smith_ast_search(
        str(FIXTURES_DIR / 'sample.py'), pattern=py_pattern, language='python'
    ))
    py_matches = json.loads(py_payload)
    assert py_matches
    assert any('NAME' in item['metavariables'] for item in py_matches)
    for item in py_matches:
        assert set(item['metavariables']).issubset({'NAME', 'ARGS'})

    go_pattern = 'func $NAME($$$ARGS) $RET { $$$BODY }'
    go_payload = run(agent_smith_ast_search(
        str(FIXTURES_DIR / 'sample.go'), pattern=go_pattern, language='go'
    ))
    go_matches = json.loads(go_payload)
    assert go_matches
    for item in go_matches:
        assert set(item['metavariables']).issubset({'NAME', 'ARGS', 'RET', 'BODY'})


def test_find_definitions_by_name_and_symbol_type() -> None:
    payload = run(agent_smith_ast_find_definitions(
        str(FIXTURES_DIR / 'sample.py'), name='area'
    ))
    data = json.loads(payload)
    assert len(data) == 1
    assert data[0]['symbol_type'] == 'method'

    payload_missing = run(agent_smith_ast_find_definitions(
        str(FIXTURES_DIR / 'sample.py'), name='nonexistent'
    ))
    assert json.loads(payload_missing) == []

    payload_wrong_type = run(agent_smith_ast_find_definitions(
        str(FIXTURES_DIR / 'sample.py'), name='area', symbol_type='class'
    ))
    assert json.loads(payload_wrong_type) == []


def test_list_imports_non_existent_path_raises_value_error() -> None:
    with pytest.raises(ValueError):
        run(agent_smith_ast_list_imports(str(FIXTURES_DIR / 'missing.py')))


def test_list_imports_empty_file_returns_empty_list(tmp_path: Path) -> None:
    empty = tmp_path / 'empty.py'
    empty.write_text('', encoding='utf-8')
    assert json.loads(run(agent_smith_ast_list_imports(str(empty)))) == []


def test_list_imports_unsupported_extension_returns_empty_list(tmp_path: Path) -> None:
    note = tmp_path / 'note.txt'
    note.write_text('import x from y', encoding='utf-8')
    assert json.loads(run(agent_smith_ast_list_imports(str(note)))) == []


def test_list_imports_directory_scan_aggregates_languages() -> None:
    payload = run(agent_smith_ast_list_imports(str(FIXTURES_DIR)))
    data = json.loads(payload)
    assert data
    scanned = {Path(item['file']).name for item in data}
    # More than one language file contributes imports.
    assert {'sample.py', 'sample.php'}.issubset(scanned)


def test_list_imports_php_and_go_return_statements() -> None:
    php_data = json.loads(
        run(agent_smith_ast_list_imports(str(FIXTURES_DIR / 'sample.php')))
    )
    assert any('use ' in item['statement'] for item in php_data)

    go_data = json.loads(
        run(agent_smith_ast_list_imports(str(FIXTURES_DIR / 'sample.go')))
    )
    assert any('import' in item['statement'] for item in go_data)
