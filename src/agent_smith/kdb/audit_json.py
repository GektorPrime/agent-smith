from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft7Validator

from agent_smith.config import get_config

VALID_HINTS = {
    None,
    'agent_smith_ast_analyze_structure',
    'agent_smith_ast_class_outline',
    'agent_smith_ast_list_imports',
    'agent_smith_ast_find_definitions',
    'agent_smith_ast_search',
}

STOP = {
    'the',
    'a',
    'an',
    'is',
    'to',
    'in',
    'or',
    'and',
    'of',
    'be',
    'not',
    'must',
    'by',
    'for',
    'from',
    'it',
    'that',
    'on',
    'at',
    'with',
    'as',
    'all',
    'when',
    'if',
    'this',
    'any',
    'its',
    'are',
    'has',
    'have',
    'do',
    'their',
    'they',
    'used',
    'use',
    'using',
    'each',
    'should',
    'may',
    'only',
    'also',
    'after',
    'both',
    'one',
    'per',
    'no',
    'such',
    'so',
    'will',
    'which',
    'into',
    'via',
    'always',
    'never',
}


def _sig(text: str) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z_'`]+", text.lower())
        if w not in STOP and len(w) > 2
    }


# [BAD-THEN] generic corpus-hygiene heuristics (domain-neutral):
#   (i)  cross-reference contamination — a THEN that points at another rule
#        instead of stating a concrete outcome (copy-paste smell).
#   (ii) non-normative THEN — a THEN with no imperative/normative token,
#        i.e. it does not actually mandate or prohibit anything.
_CROSS_REF_PATTERN = re.compile(
    r'\b('
    r'see\s+(?:the\s+)?(?:above|below|section|rule)'
    r'|(?:as\s+)?(?:described|defined|noted|stated|documented|covered)'
    r'\s+(?:in|above|below|elsewhere)'
    r'|(?:per|refer\s+to|according\s+to)\s+(?:the\s+)?'
    r'(?:above|below|section|rule|convention|guideline)'
    r'|follow(?:ing)?\s+(?:the\s+)?(?:convention|rule|guideline)s?\b'
    r')\b',
    re.IGNORECASE,
)

_NORMATIVE_PATTERN = re.compile(
    r'\b('
    r'must(?:\s+not)?|shall(?:\s+not)?|may\s+not|cannot|can\'t'
    r'|required|prohibited|forbidden|mandatory|never|always'
    r'|do\s+not|don\'t|should(?:\s+not)?'
    r')\b',
    re.IGNORECASE,
)


def _bad_then(then: str) -> str | None:
    """Return a reason string when a bdd.then is a corpus-hygiene smell."""
    text = then.strip()
    if not text:
        return None
    if _CROSS_REF_PATTERN.search(text):
        return 'cross-reference (points at another rule, not a concrete outcome)'
    if not _NORMATIVE_PATTERN.search(text):
        return 'non-normative (no MUST/MUST NOT/required/prohibited token)'
    return None


def _schema_path() -> Path:
    return get_config().home() / 'lore' / 'json' / 'schema.json'


def _scenario_files() -> list[Path]:
    return sorted(get_config().scenarios_path().rglob('*.json'))


def run_audit(files: list[Path] | None = None) -> tuple[int, list[str]]:
    schema_path = _schema_path()
    with schema_path.open(encoding='utf-8') as fh:
        schema = json.load(fh)

    validator = Draft7Validator(schema)
    issues: list[str] = []
    json_files = files if files is not None else _scenario_files()

    for file_path in json_files:
        try:
            with file_path.open(encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            issues.append(f'[PARSE]           {file_path.name}: {exc}')
            continue

        for err in validator.iter_errors(data):
            path = list(err.absolute_path)
            sc_id = '?'
            if path and isinstance(path[0], int):
                try:
                    sc_id = data[path[0]].get('id', '?')
                except Exception:  # noqa: BLE001
                    pass
            issues.append(
                f'[SCHEMA]          {file_path.name} [{sc_id}]: {err.message}'
                f' (path: {" -> ".join(str(p) for p in path)})'
            )

        if not isinstance(data, list):
            continue

        for sc in data:
            sid = sc.get('id', '?')
            stype = sc.get('type', 'rule')
            bdd = sc.get('bdd', {})
            then = bdd.get('then', '')
            vr = sc.get('verbatim_rule', '')
            expl = sc.get('explanation', '')
            if vr.strip().endswith(':') and len(vr.strip()) < 60:
                issues.append(f'[STUB-VR]         {sid}: {vr!r}')

            if stype == 'rule':
                bad_then_reason = _bad_then(then)
                if bad_then_reason is not None:
                    issues.append(f'[BAD-THEN]        {sid}: {bad_then_reason}')

                ts, vs = _sig(then), _sig(vr)
                if ts and vs and len(ts & vs) == 0:
                    issues.append(
                        f'[VR-MISMATCH]     {sid}: '
                        f'then={sorted(ts)[:4]} vr={sorted(vs)[:4]}'
                    )

            sentences = [
                s.strip()
                for s in re.split(r'(?<=[.!?])\s+', expl)
                if len(s.strip()) > 25
            ]
            if stype == 'rule' and len(sentences) < 2:
                issues.append(
                    f'[SHORT-EXPL]      {sid}: '
                    f'{len(sentences)} sentence(s): {expl[:80]}'
                )

            if sc.get('mcp_tool_hint') not in VALID_HINTS:
                issues.append(f'[BAD-HINT]        {sid}: {sc.get("mcp_tool_hint")!r}')

    return len(json_files), issues


def main() -> int:
    from agent_smith.config import configure_cli_environment

    configure_cli_environment()
    count, issues = run_audit()
    print(f'Checked {count} files.')
    if issues:
        print(f'{len(issues)} issue(s):\n')
        for issue in issues:
            print(f'  {issue}')
        return 1
    print('CLEAN — all 7 checks passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
