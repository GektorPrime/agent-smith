from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ast_grep_py import SgNode, SgRoot

from agent_smith.ast_tools.language import EXTENSION_TO_LANGUAGE, resolve_language


@dataclass(frozen=True)
class PatternSpec:
    symbol_type: str
    pattern: str
    name_meta: str | None = 'NAME'


SYMBOL_PATTERNS: dict[str, list[PatternSpec]] = {
    'python': [
        PatternSpec('class', 'class $NAME: $$$BODY'),
        PatternSpec('class', 'class $NAME($$$BASES): $$$BODY'),
        PatternSpec('function', 'def $NAME($$$ARGS)'),
        PatternSpec('function', 'async def $NAME($$$ARGS)'),
        PatternSpec('variable', '$NAME = $VALUE'),
        PatternSpec('import', 'import $$$ITEMS', None),
        PatternSpec('import', 'from $MODULE import $$$ITEMS', None),
    ],
    'typescript': [
        PatternSpec('class', 'interface $NAME { $$$BODY }'),
        PatternSpec('class', 'class $NAME { $$$BODY }'),
        PatternSpec('class', 'class $NAME implements $$$BASES { $$$BODY }'),
        PatternSpec('function', 'function $NAME($$$ARGS) { $$$BODY }'),
        PatternSpec('variable', 'let $NAME = $VALUE'),
        PatternSpec('variable', 'var $NAME = $VALUE'),
        PatternSpec('constant', 'const $NAME = $VALUE'),
        PatternSpec('import', 'import $$$ITEMS from $SOURCE', None),
        PatternSpec('import', 'import $SOURCE', None),
    ],
    'javascript': [
        PatternSpec('class', 'class $NAME { $$$BODY }'),
        PatternSpec('function', 'function $NAME($$$ARGS) { $$$BODY }'),
        PatternSpec('variable', 'let $NAME = $VALUE'),
        PatternSpec('variable', 'var $NAME = $VALUE'),
        PatternSpec('constant', 'const $NAME = $VALUE'),
        PatternSpec('import', 'import $$$ITEMS from $SOURCE', None),
        PatternSpec('import', 'import $SOURCE', None),
    ],
    'php': [
        PatternSpec('class', 'class $NAME { $$$BODY }'),
        PatternSpec('function', 'function $NAME($$$ARGS) { $$$BODY }'),
        PatternSpec('constant', 'const $NAME = $VALUE'),
        PatternSpec('import', 'use $NAME;', None),
        PatternSpec('import', 'require($EXPR);', None),
        PatternSpec('import', 'include($EXPR);', None),
    ],
    'go': [
        PatternSpec('class', 'type $NAME struct { $$$BODY }'),
        PatternSpec('class', 'type $NAME interface { $$$BODY }'),
        PatternSpec('function', 'func $NAME($$$ARGS) { $$$BODY }'),
        PatternSpec('method', 'func ($RECV) $NAME($$$ARGS) { $$$BODY }'),
        PatternSpec('variable', 'var $NAME = $VALUE'),
        PatternSpec('constant', 'const $NAME = $VALUE'),
        PatternSpec('import', 'import ($$$ITEMS)', None),
        PatternSpec('import', 'import $ITEM', None),
    ],
    'rust': [
        PatternSpec('class', 'struct $NAME { $$$BODY }'),
        PatternSpec('function', 'fn $NAME($$$ARGS) { $$$BODY }'),
        PatternSpec('constant', 'const $NAME: $TYPE = $VALUE;'),
        PatternSpec('import', 'use $ITEM;', None),
    ],
    'java': [
        PatternSpec('class', 'class $NAME { $$$BODY }'),
        PatternSpec('class', 'interface $NAME { $$$BODY }'),
        PatternSpec('method', '$RET $NAME($$$ARGS) { $$$BODY }'),
        PatternSpec('import', 'import $ITEM;', None),
    ],
    'ruby': [
        PatternSpec('class', 'class $NAME\n$$$BODY\nend'),
        PatternSpec('function', 'def $NAME($$$ARGS)\n$$$BODY\nend'),
        PatternSpec('import', 'require $ITEM', None),
        PatternSpec('import', 'require_relative $ITEM', None),
    ],
    'c-sharp': [
        PatternSpec('class', 'class $NAME { $$$BODY }'),
        PatternSpec('method', '$RET $NAME($$$ARGS) { $$$BODY }'),
        PatternSpec('constant', 'const $TYPE $NAME = $VALUE;'),
        PatternSpec('import', 'using $ITEM;', None),
    ],
}


def _supported_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    files: list[Path] = []
    for candidate in path.rglob('*'):
        if candidate.is_file() and candidate.suffix.lower() in EXTENSION_TO_LANGUAGE:
            files.append(candidate)
    return files


def _first_line(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ''
    return line[:500]


def _line_range(node: SgNode) -> tuple[int, int]:
    node_range = node.range()
    return node_range.start.line + 1, node_range.end.line + 1


def _extract_name(node: SgNode) -> str:
    name_node = node.get_match('NAME')
    if name_node is None:
        return ''
    return name_node.text().strip()


def _range_within(
    inner_start: int, inner_end: int, outer_start: int, outer_end: int
) -> bool:
    return outer_start <= inner_start and inner_end <= outer_end


def _collect_for_file(file_path: Path, language: str) -> list[dict[str, object]]:
    source = file_path.read_text(encoding='utf-8', errors='replace')
    root = SgRoot(source, language).root()
    specs = SYMBOL_PATTERNS.get(language, [])
    results: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, int, str]] = set()
    class_ranges: list[tuple[int, int]] = []

    class_specs = [spec for spec in specs if spec.symbol_type == 'class']
    for spec in class_specs:
        for node in root.find_all(pattern=spec.pattern):
            name = _extract_name(node)
            if not name:
                continue

            start_line, end_line = _line_range(node)
            class_ranges.append((start_line, end_line))
            signature = _first_line(node.text())
            key = (str(file_path), spec.symbol_type, start_line, end_line, name)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    'name': name,
                    'symbol_type': spec.symbol_type,
                    'file': str(file_path),
                    'start_line': start_line,
                    'end_line': end_line,
                    'text': signature,
                }
            )

    for spec in specs:
        if spec.symbol_type == 'class':
            continue

        for node in root.find_all(pattern=spec.pattern):
            symbol_type = spec.symbol_type
            name = _extract_name(node) if spec.name_meta else _first_line(node.text())
            if spec.name_meta and not name:
                continue

            start_line, end_line = _line_range(node)
            if symbol_type == 'function':
                if any(
                    _range_within(start_line, end_line, c_start, c_end)
                    for c_start, c_end in class_ranges
                ):
                    symbol_type = 'method'

            if symbol_type == 'variable' and name.isupper():
                symbol_type = 'constant'

            signature = _first_line(node.text())
            key = (str(file_path), symbol_type, start_line, end_line, name)
            if key in seen:
                continue
            seen.add(key)

            results.append(
                {
                    'name': name,
                    'symbol_type': symbol_type,
                    'file': str(file_path),
                    'start_line': start_line,
                    'end_line': end_line,
                    'text': signature,
                }
            )

    return results


def _collect_structure_entries(path: str, language: str | None = None) -> list[dict[str, object]]:
    input_path = Path(path)
    if not input_path.exists():
        raise ValueError(f'Path does not exist: {path}')

    entries: list[dict[str, object]] = []
    for file_path in _supported_files(input_path):
        try:
            resolved_language = resolve_language(str(file_path), language)
        except ValueError:
            continue
        entries.extend(_collect_for_file(file_path, resolved_language))
    return entries


async def agent_smith_ast_analyze_structure(
    path: str, language: str | None = None
) -> str:
    entries = _collect_structure_entries(path=path, language=language)
    return json.dumps(entries, ensure_ascii=False)


async def agent_smith_ast_class_outline(
    path: str, class_name: str | None = None, language: str | None = None
) -> str:
    input_path = Path(path)
    if not input_path.exists():
        raise ValueError(f'Path does not exist: {path}')

    classes: list[dict[str, object]] = []
    files = _supported_files(input_path)
    all_entries = _collect_structure_entries(path=path, language=language)

    for file_path in files:
        try:
            resolved_language = resolve_language(str(file_path), language)
        except ValueError:
            continue

        source = file_path.read_text(encoding='utf-8', errors='replace')
        root = SgRoot(source, resolved_language).root()
        file_entries = [entry for entry in all_entries if entry['file'] == str(file_path)]

        for class_spec in (
            s for s in SYMBOL_PATTERNS.get(resolved_language, []) if s.symbol_type == 'class'
        ):
            for class_node in root.find_all(pattern=class_spec.pattern):
                name = _extract_name(class_node)
                if not name:
                    continue
                if class_name and name != class_name:
                    continue

                start_line, end_line = _line_range(class_node)
                members: list[dict[str, object]] = []

                for entry in file_entries:
                    if entry['symbol_type'] == 'class':
                        continue
                    entry_start = int(entry['start_line'])
                    entry_end = int(entry['end_line'])
                    if not _range_within(entry_start, entry_end, start_line, end_line):
                        continue

                    member_type = str(entry['symbol_type'])
                    if member_type in {'variable', 'constant'}:
                        member_type = 'field'
                    members.append(
                        {
                            'name': entry['name'],
                            'symbol_type': member_type,
                            'text': entry['text'],
                        }
                    )

                classes.append(
                    {
                        'name': name,
                        'file': str(file_path),
                        'start_line': start_line,
                        'end_line': end_line,
                        'members': members,
                    }
                )

    return json.dumps(classes, ensure_ascii=False)


async def agent_smith_ast_list_imports(path: str, language: str | None = None) -> str:
    input_path = Path(path)
    if not input_path.exists():
        raise ValueError(f'Path does not exist: {path}')

    imports: list[dict[str, object]] = []
    for file_path in _supported_files(input_path):
        try:
            resolved_language = resolve_language(str(file_path), language)
        except ValueError:
            continue
        source = file_path.read_text(encoding='utf-8', errors='replace')
        root = SgRoot(source, resolved_language).root()

        import_specs = [
            s for s in SYMBOL_PATTERNS.get(resolved_language, []) if s.symbol_type == 'import'
        ]
        for spec in import_specs:
            for node in root.find_all(pattern=spec.pattern):
                start_line, end_line = _line_range(node)
                imports.append(
                    {
                        'file': str(file_path),
                        'statement': node.text().strip(),
                        'start_line': start_line,
                        'end_line': end_line,
                    }
                )

    return json.dumps(imports, ensure_ascii=False)
