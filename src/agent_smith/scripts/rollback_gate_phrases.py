"""
Revert gate-phrase-only changes in gate-carrying files.

Ports the git-diff-aware rollback algorithm: for each gate file, inspect
the working-tree diff against HEAD. If every changed line is an AGENT GATE
marker line (only the token differs), the file is restored from HEAD.
Files with any substantive change are left untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_smith.config import get_config
from agent_smith.mcp.gates.regenerate import GATE_PATTERN, _discover_gate_files


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['git', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _changed_lines(diff_output: str) -> list[str]:
    """
    Extract added/removed content lines from a unified diff,
    excluding the +++/--- file headers.
    """
    lines: list[str] = []
    for line in diff_output.splitlines():
        if line.startswith(('+++', '---')):
            continue
        if line.startswith(('+', '-')):
            lines.append(line)
    return lines


def _is_gate_only(changed_lines: list[str]) -> bool:
    """
    Return True when every changed line is an AGENT GATE marker line.
    """
    return all(GATE_PATTERN.search(line[1:]) for line in changed_lines)


def _inside_git_work_tree(project_root: Path) -> bool:
    result = _git(['rev-parse', '--is-inside-work-tree'], cwd=project_root)
    return result.returncode == 0 and result.stdout.strip() == 'true'


def main() -> int:
    from agent_smith.config import configure_cli_environment

    configure_cli_environment()
    config = get_config()
    project_root = config.project_root()

    if not _inside_git_work_tree(project_root):
        print(
            'Gate rollback requires a git repository (it reverts token-only '
            'changes via git). Non-git project detected — nothing to do.'
        )
        return 0

    reverted = 0
    skipped = 0

    for filepath in _discover_gate_files():
        relpath = filepath.relative_to(config.home()).as_posix()

        diff = _git(['diff', 'HEAD', '-U0', '--', str(filepath)], cwd=project_root)
        if diff.returncode != 0:
            print(f'Skipped {relpath}: git diff failed ({diff.stderr.strip()}).')
            skipped += 1
            continue

        changed_lines = _changed_lines(diff.stdout)
        if not changed_lines:
            continue

        if _is_gate_only(changed_lines):
            checkout = _git(['checkout', 'HEAD', '--', str(filepath)], cwd=project_root)
            if checkout.returncode != 0:
                print(f'Skipped {relpath}: git checkout failed ({checkout.stderr.strip()}).')
                skipped += 1
                continue
            print(f'Reverted {relpath}: gate-phrase-only change.')
            reverted += 1
        else:
            print(f'Skipped {relpath}: non-gate diff detected.')
            skipped += 1

    print(f'\nReverted {reverted} file(s), skipped {skipped} file(s).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
