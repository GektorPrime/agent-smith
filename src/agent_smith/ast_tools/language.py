from __future__ import annotations

from pathlib import Path

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    '.py': 'python',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.php': 'php',
    '.go': 'go',
    '.rs': 'rust',
    '.java': 'java',
    '.rb': 'ruby',
    '.cs': 'c-sharp',
}


def detect_language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    language = EXTENSION_TO_LANGUAGE.get(suffix)
    if language is None:
        supported = ', '.join(sorted(EXTENSION_TO_LANGUAGE))
        raise ValueError(
            f'Cannot detect AST language for {path!r}. '
            f'Supported extensions: {supported}'
        )
    return language


def resolve_language(path: str, language: str | None) -> str:
    if language:
        return language
    return detect_language(path)
