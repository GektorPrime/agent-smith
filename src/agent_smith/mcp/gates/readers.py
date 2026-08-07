"""Rule-file readers and init flow assembly for the Agent Smith MCP server.

Rules-protocol reading is server-driven and non-skippable. The agent does not
decide how much to read: after selecting a task-scoped set of rule files (or
falling back to all of them), it is walked file-by-file through a mandated
chain of `read_rules` calls. Each call serves exactly one complete file so no
single response can be truncated at the MCP transport boundary. A per-session
ledger records which files were served and which gate tokens they carried;
`handoff` refuses to close until every mandated file was served AND the agent
echoes back every gate token it was shown.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_smith.config import get_config
from agent_smith.mcp.gates.regenerate import GATE_PATTERN, regenerate

# ---------------------------------------------------------------------------
# Per-session read state
#
# The MCP server runs as a long-lived local (stdio) process, one per session,
# so module-level state persists across tool calls within a session. `init`
# resets it, which is what makes a fresh `/agent-smith-init` start a clean
# read cycle.
# ---------------------------------------------------------------------------


class _ReadState:
    def __init__(self) -> None:
        self.mandated: list[Path] = []
        self.cursor: int = 0
        self.served_tokens: dict[str, str] = {}
        self.pinned: bool = False

    def reset(self) -> None:
        self.mandated = []
        self.cursor = 0
        self.served_tokens = {}
        self.pinned = False


_state = _ReadState()


def _read_file(filepath: Path) -> str:
    """
    Read a file and return its contents, or an error message.
    """
    try:
        return filepath.read_text(encoding='utf-8')
    except FileNotFoundError:
        return f'ERROR: File not found: {filepath}'
    except Exception as exc:
        return f'ERROR: {exc}'


def _extract_token(content: str) -> str | None:
    """
    Return the current AGENT GATE token embedded in file content, if any.
    """
    match = GATE_PATTERN.search(content)
    if match is None:
        return None
    inner = re.search(r'Include the phrase "([^"]+)"', match.group(0))
    return inner.group(1) if inner else None


def _all_rule_files() -> list[Path]:
    """
    Return every rule markdown file under rules_md/, recursively, sorted.
    """
    rules_dir = get_config().rules_md_path()
    if not rules_dir.is_dir():
        return []
    return sorted(p for p in rules_dir.rglob('*.md') if p.is_file())


def _core_rule_files() -> list[Path]:
    """
    Return every markdown file under rules_md/core/ (always-pinned base).
    May be empty when the core folder is absent or empty.
    """
    core_dir = get_config().rules_core_path()
    if not core_dir.is_dir():
        return []
    return sorted(p for p in core_dir.rglob('*.md') if p.is_file())


def init() -> str:
    """
    Regenerate gate phrases, reset read state, and return mandatory next-step
    instructions.
    """
    _state.reset()

    sections: list[str] = ['=== GATE REGENERATION ===']

    try:
        regeneration_output = regenerate()
        sections.append(regeneration_output if regeneration_output else '(no output)')
    except Exception as exc:
        sections.append(f'ERROR: Failed to regenerate gates: {exc}')
        sections.append(
            '=== HALTED ===\n'
            'Gate regeneration failed. Do NOT proceed with the rule-reading protocol.\n'
            'Surface this error to the user and stop.'
        )
        return '\n\n'.join(sections)
    else:
        sections.append('=== NEXT STEPS ===')
        sections.append(
            'Gate phrases have been regenerated. Complete the reading protocol in\n'
            'this exact order:\n'
            '\n'
            '  1. Call agent_smith_read_entry_point.\n'
            '  2. Call agent_smith_read_persona.\n'
            '  3. Call agent_smith_read_routing_table.\n'
            '  4. Match the current task against the routing table, then call\n'
            '     agent_smith_select_rules with the relative paths from every\n'
            '     matching row (combine multiple rows). If nothing matches or you\n'
            '     are unsure, call it with an empty list to read all rules.\n'
            '  5. Call agent_smith_read_rules repeatedly. Each call serves one\n'
            '     rule file and tells you whether to call it again. Keep calling\n'
            '     until it returns the DONE marker. You MUST NOT stop early.\n'
            '  6. Post your acknowledgment as compact <filename>: <gate-phrase>\n'
            '     lines per the format mandated in entry-point.md.\n'
            '  7. In the same response as that acknowledgment, call\n'
            '     agent_smith_handoff and pass every gate phrase you collected in\n'
            '     its acknowledged_tokens argument.'
        )
        return '\n\n'.join(sections)


def handoff(acknowledged_tokens: list[str] | None = None) -> str:
    """
    Verify the read ledger, then return the post-acknowledgment directive.

    Verification has two parts:
    - Delivery: every mandated rule file must have been served.
    - Capture: acknowledged_tokens must include every token served during the
      read chain PLUS the entry-point and persona tokens.

    On failure a corrective directive is returned and execution is NOT handed
    off.
    """
    provided = set(acknowledged_tokens or [])

    # Delivery check: the mandated chain must have run to completion.
    if not _state.pinned:
        return (
            'HANDOFF REFUSED: the rule-reading chain has not started. '
            'Call agent_smith_select_rules, then drive agent_smith_read_rules to '
            'completion before calling agent_smith_handoff.'
        )
    if _state.cursor < len(_state.mandated):
        remaining = len(_state.mandated) - _state.cursor
        return (
            f'HANDOFF REFUSED: {remaining} mandated rule file(s) not yet served. '
            'Call agent_smith_read_rules again until it returns the DONE marker.'
        )

    # Capture check: every token the agent was shown must be echoed back.
    required = dict(_state.served_tokens)

    entry_token = _extract_token(_read_file(get_config().entry_point_path()))
    if entry_token:
        required['entry-point.md'] = entry_token

    persona_file = get_config().home() / 'persona.md'
    if persona_file.is_file():
        persona_token = _extract_token(_read_file(persona_file))
        if persona_token:
            required['persona.md'] = persona_token

    missing = [name for name, token in required.items() if token not in provided]
    if missing:
        return (
            'HANDOFF REFUSED: acknowledgment is missing gate phrases for the '
            f'following file(s): {", ".join(sorted(missing))}. '
            'Re-read any file whose gate phrase you cannot produce, then call '
            'agent_smith_handoff again with every gate phrase in acknowledged_tokens.'
        )

    return (
        'Reading protocol acknowledged and complete. '
        "Your sole remaining obligation is the user's stated task. "
        'Begin executing that task immediately using the tools available in your current role. '
        'Do not emit another text-only response until the task is complete or you genuinely need '
        'clarification from the user. '
        'If no task has been stated yet, ask the user what they want to do.'
    )


def read_entry_point() -> str:
    """
    Return entry-point.md content.
    """
    filepath = get_config().entry_point_path()
    return f'=== entry-point.md ===\n\n{_read_file(filepath)}'


def read_persona() -> str:
    """
    Return persona.md content if present, else the conditional absence message.
    """
    persona_file = get_config().home() / 'persona.md'
    if persona_file.is_file():
        return f'=== persona.md ===\n\n{_read_file(persona_file)}'
    return (
        '=== persona.md (conditional) ===\n\n'
        'File absent. No communication style modifications required.'
    )


def read_routing_table() -> str:
    """
    Return the host-maintained routing table content, or an absence notice.
    """
    filepath = get_config().routing_table_path()
    if filepath.is_file():
        return f'=== rules_routing_table.md ===\n\n{_read_file(filepath)}'
    return (
        '=== rules_routing_table.md (absent) ===\n\n'
        'No routing table found. All rule files are mandatory this session. '
        'Call agent_smith_select_rules with an empty list to read every rule.'
    )


def _normalize_selection(selected_paths: list[str]) -> list[Path] | None:
    """
    Resolve agent-supplied relative paths against the actual rule-file walk.

    Returns the matched Path objects (order-preserving, deduped), or None if
    any supplied path fails to resolve — signalling a hard fallback to all
    rules. An empty selection also returns None (unsure -> all).
    """
    if not selected_paths:
        return None

    rules_dir = get_config().rules_md_path()
    valid = {p.relative_to(rules_dir).as_posix(): p for p in _all_rule_files()}

    matched: list[Path] = []
    seen: set[str] = set()
    for raw in selected_paths:
        rel = raw.strip().lstrip('./')
        # Tolerate paths that include the rules_md/ prefix.
        if rel.startswith('rules_md/'):
            rel = rel[len('rules_md/') :]
        if rel not in valid:
            return None  # hard fallback: any miss -> read all
        if rel not in seen:
            seen.add(rel)
            matched.append(valid[rel])
    return matched


def select_rules(selected_paths: list[str] | None = None) -> str:
    """
    Pin the mandated set of rule files for this session's read chain.

    The mandated set is the union of the always-read core files and the agent's
    validated selection, deduped and ordered. Any invalid path, an empty
    selection, or a missing routing table results in a hard fallback to all
    rule files. Pinning latches once per read cycle; later calls are ignored
    until the next init.
    """
    if _state.pinned:
        return (
            f'Selection already pinned: {len(_state.mandated)} rule file(s). '
            'Continue calling agent_smith_read_rules until DONE.'
        )

    selected = list(selected_paths or [])
    core = _core_rule_files()

    matched = _normalize_selection(selected)

    fallback = matched is None
    if fallback:
        mandated = _all_rule_files()
    else:
        # Union of core (always) + selection, order-preserving dedup.
        mandated = []
        seen: set[Path] = set()
        for filepath in [*core, *matched]:
            if filepath not in seen:
                seen.add(filepath)
                mandated.append(filepath)

    _state.mandated = mandated
    _state.cursor = 0
    _state.served_tokens = {}
    _state.pinned = True

    if not mandated:
        return (
            'No rule files found. Nothing to read. '
            'Post an empty acknowledgment and call agent_smith_handoff.'
        )

    if fallback:
        reason = (
            'no selection provided or a selected path did not resolve; '
            'reading ALL rule files'
        )
    else:
        reason = (
            f'{len(matched or [])} selected + {len(core)} core file(s), deduped'
        )

    return (
        f'Pinned {len(mandated)} mandated rule file(s) ({reason}). '
        'Now call agent_smith_read_rules repeatedly until it returns DONE. '
        'You MUST NOT stop early.'
    )


def read_rules() -> str:
    """
    Serve exactly one mandated rule file per call, advancing a server-owned
    cursor. If no selection was pinned, auto-pin all rule files. Each response
    carries one complete file plus a directive to call again, or the DONE
    marker once every mandated file has been served.
    """
    if not _state.pinned:
        # Safety: agent skipped select_rules. Pin everything.
        select_rules([])

    if not _state.mandated:
        return (
            '=== DONE ===\n\n'
            'No rule files to read. Post an empty acknowledgment and call '
            'agent_smith_handoff.'
        )

    if _state.cursor >= len(_state.mandated):
        return (
            '=== DONE ===\n\n'
            'All mandated rule files have already been served. Post your '
            'acknowledgment and call agent_smith_handoff with every gate phrase '
            'in acknowledged_tokens.'
        )

    rules_dir = get_config().rules_md_path()
    filepath = _state.mandated[_state.cursor]
    header = filepath.relative_to(rules_dir).as_posix()
    content = _read_file(filepath)

    token = _extract_token(content)
    if token:
        _state.served_tokens[header] = token

    _state.cursor += 1
    served = _state.cursor
    total = len(_state.mandated)

    body = f'=== {header} ===\n\n{content}'

    if served < total:
        directive = (
            f'\n\n=== NEXT ({served} of {total}) ===\n'
            'You MUST now call agent_smith_read_rules again to receive the next '
            'mandated rule file. Do NOT stop until you receive the DONE marker.'
        )
    else:
        directive = (
            f'\n\n=== DONE ({served} of {total}) ===\n'
            'All mandated rule files have been served. Post your acknowledgment '
            'as compact <filename>: <gate-phrase> lines, then call '
            'agent_smith_handoff with every gate phrase in acknowledged_tokens.'
        )

    return body + directive
