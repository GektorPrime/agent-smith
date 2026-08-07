"""
Regenerate AGENT GATE phrases in all gate-carrying files under $AGENT_SMITH_HOME.

This module finds all AGENT GATE comments in the gate files and replaces
the existing phrase with a newly generated random token. A fresh token is
generated per document so each file carries its own unique gate phrase.
"""

from __future__ import annotations

import re
import secrets
import string
from pathlib import Path

from agent_smith.config import get_config

GATE_PATTERN = re.compile(
    r'(<!-- AGENT GATE: Include the phrase ")[^"]+(" in your reading acknowledgment to confirm this file was read to completion\. -->)'
)

_TOKEN_ALPHABET = string.ascii_uppercase + string.digits
_TOKEN_GROUPS = 3
_TOKEN_GROUP_LENGTH = 4


def generate_token() -> str:
    """
    Generate a random gate token like '7FQ2-K9XM-P3RD'.
    """
    groups = [
        ''.join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_GROUP_LENGTH))
        for _ in range(_TOKEN_GROUPS)
    ]
    return '-'.join(groups)


def _discover_gate_files() -> list[Path]:
    """
    Return all gate-carrying files under $AGENT_SMITH_HOME:
    entry-point.md, persona.md (if present), and every .md under lore/rules_md/
    (recursively, including symlinked rule files in batch subdirectories).
    """
    config = get_config()
    files: list[Path] = []

    entry_point = config.entry_point_path()
    if entry_point.is_file():
        files.append(entry_point)

    persona = config.home() / 'persona.md'
    if persona.is_file():
        files.append(persona)

    rules_dir = config.rules_md_path()
    if rules_dir.is_dir():
        files.extend(sorted(p for p in rules_dir.rglob('*.md') if p.is_file()))

    return files


def regenerate() -> str:
    """
    Regenerate AGENT GATE phrases and return the full textual report.
    """
    home = get_config().home()
    updated_count = 0
    output_lines: list[str] = []

    for filepath in _discover_gate_files():
        relpath = filepath.relative_to(home).as_posix()
        content = filepath.read_text(encoding='utf-8')

        new_token = generate_token()
        new_content, count = GATE_PATTERN.subn(rf'\g<1>{new_token}\g<2>', content)

        if count == 0:
            output_lines.append(
                f'WARNING: No AGENT GATE comment found in {relpath}, skipping.'
            )
            continue

        filepath.write_text(new_content, encoding='utf-8')

        updated_count += 1
        output_lines.append(f'UPDATED {relpath}: gate phrase regenerated.')

    if updated_count:
        output_lines.append(f'\nRegenerated {updated_count} gate phrase(s).')
    else:
        output_lines.append('\nNo files were updated.')

    return '\n'.join(output_lines)


if __name__ == '__main__':
    print(regenerate())
