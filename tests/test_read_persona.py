from __future__ import annotations

from pathlib import Path

from agent_smith.mcp.gates.readers import read_persona


def test_read_persona_present(gated_home: Path) -> None:
    result = read_persona()
    assert result.startswith('=== persona.md ===\n\n')
    assert '# Persona' in result


def test_read_persona_absent(gated_home: Path) -> None:
    (gated_home / 'persona.md').unlink()
    result = read_persona()
    assert result == (
        '=== persona.md (conditional) ===\n\n'
        'File absent. No communication style modifications required.'
    )
