from __future__ import annotations

import json
import re
from pathlib import Path

from ast_grep_py import SgRoot

from agent_smith.ast_tools.language import resolve_language
from agent_smith.ast_tools.structure import _collect_structure_entries


_META_TOKEN_RE = re.compile(r'\$\$\$([A-Za-z_][A-Za-z0-9_]*)|\$([A-Za-z_][A-Za-z0-9_]*)')


def _pattern_metavariables(pattern: str) -> tuple[set[str], set[str]]:
    single: set[str] = set()
    multiple: set[str] = set()
    for match in _META_TOKEN_RE.finditer(pattern):
        variadic = match.group(1)
        scalar = match.group(2)
        if variadic:
            multiple.add(variadic)
        elif scalar:
            single.add(scalar)
    return single, multiple


async def agent_smith_ast_find_definitions(
    path: str,
    name: str,
    symbol_type: str | None = None,
    language: str | None = None,
) -> str:
    entries = _collect_structure_entries(path=path, language=language)
    filtered = [entry for entry in entries if entry['name'] == name]
    if symbol_type:
        filtered = [entry for entry in filtered if entry['symbol_type'] == symbol_type]
    return json.dumps(filtered, ensure_ascii=False)


async def agent_smith_ast_search(
    path: str, pattern: str, language: str, k: int = 50
) -> str:
    input_path = Path(path)
    if not input_path.exists():
        raise ValueError(f'Path does not exist: {path}')

    files: list[Path] = [input_path] if input_path.is_file() else [p for p in input_path.rglob('*') if p.is_file()]
    results: list[dict[str, object]] = []
    single_vars, multiple_vars = _pattern_metavariables(pattern)

    for file_path in files:
        if len(results) >= k:
            break

        try:
            resolved_language = resolve_language(str(file_path), language)
        except ValueError:
            continue

        source = file_path.read_text(encoding='utf-8', errors='replace')
        root = SgRoot(source, resolved_language).root()
        for node in root.find_all(pattern=pattern):
            if len(results) >= k:
                break

            node_range = node.range()
            metavariables: dict[str, object] = {}
            for meta in sorted(single_vars):
                single = node.get_match(meta)
                if single is not None:
                    metavariables[meta] = single.text()
            for meta in sorted(multiple_vars):
                multiple = node.get_multiple_matches(meta)
                if multiple:
                    metavariables[meta] = [m.text() for m in multiple]

            results.append(
                {
                    'file': str(file_path),
                    'start_line': node_range.start.line + 1,
                    'end_line': node_range.end.line + 1,
                    'text': node.text().strip(),
                    'metavariables': metavariables,
                }
            )

    return json.dumps(results, ensure_ascii=False)
