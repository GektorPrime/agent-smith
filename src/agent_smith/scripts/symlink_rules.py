"""
Symlink external rules documentation into .agent-smith/lore/rules_md/.

Takes a path to rules documentation — either a folder (with arbitrary
subfolders) or a single .md file — and mirrors its markdown content into
.agent-smith/lore/rules_md/ as absolute symlinks.

Batches are namespaced by <name> (the source basename by default, overridable
with --name):
  - a directory source lands under rules_md/<name>/, mirroring its subfolders;
  - a single file links flat as rules_md/<name>.md.
Re-running the same name refreshes only that batch; different names accumulate
side by side, so documentation can be linked in batches from several sources.

Host-authored real files are never clobbered — a link that would collide with
an existing real file is skipped with a warning. Only markdown (.md) files are
linked; the mirrored subfolder structure is recreated as needed.

Console script: agent-smith-symlink-rules
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

RULES_MD_REL = ('.agent-smith', 'lore', 'rules_md')


class SymlinkRulesError(RuntimeError):
    pass


def _resolve_repo_root(explicit: str | None) -> Path:
    # An explicit --repo-root is trusted as-is: non-git projects are supported
    # when the user points at the root themselves.
    if explicit:
        root = Path(explicit)
        if not root.is_dir():
            raise SymlinkRulesError(f'--repo-root does not exist: {explicit}')
        return root.resolve()

    # Auto-discovery walks up to the nearest .git anchor.
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / '.git').exists():
            return candidate

    raise SymlinkRulesError(
        'Not inside a git repository (auto-discovery anchors on .git).\n'
        "  - cd into your project's repository and re-run, or\n"
        '  - for a non-git project, pass the root explicitly: --repo-root <path>'
    )


def _resolve_source(raw: str) -> tuple[Path, Path]:
    """
    Return (target, name_basis) for the source.

    ``target`` is the fully resolved path used as the symlink target (so links
    point at the real file, not an intermediate symlink chain). ``name_basis``
    keeps the user-provided path — only made absolute, without following
    symlinks — so the batch name reflects what the user typed rather than the
    canonical target it happens to resolve to.
    """
    provided = Path(raw).expanduser()
    if not provided.exists():
        raise SymlinkRulesError(f'Source path does not exist: {raw}')

    target = provided.resolve()
    name_basis = provided if provided.is_absolute() else Path.cwd() / provided
    # Strip a trailing '.'/'..'/redundant separators without resolving symlinks.
    name_basis = Path(os.path.normpath(name_basis))

    if target.is_file() and target.suffix != '.md':
        raise SymlinkRulesError(
            f'Source file must be a markdown (.md) file: {raw}'
        )
    return target, name_basis


def _resolve_name(target: Path, name_basis: Path, explicit: str | None) -> str:
    if explicit:
        name = explicit
    elif target.is_file():
        name = name_basis.stem
    else:
        name = name_basis.name
    name = name.strip().strip('/')
    if not name or name in {'.', '..'} or '/' in name or '\\' in name:
        raise SymlinkRulesError(
            f'Invalid batch name: {name!r}. Use --name to provide a simple '
            'directory name without path separators.'
        )
    return name


def _prune_stale_links(base: Path, keep: set[Path], counters: dict[str, int]) -> None:
    """
    Remove symlinks under ``base`` that are not in ``keep`` (i.e. no longer
    map to a current source file), then prune the empty directories that
    removal leaves behind. Real files are left untouched.
    """
    if not base.exists():
        return

    for path in sorted(
        (p for p in base.rglob('*') if p.is_symlink()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if path not in keep:
            path.unlink()
            print(f'  [prune] {path.name} — source no longer present')
            counters['pruned'] += 1

    _prune_empty_dirs(base)


def _prune_empty_dirs(base: Path) -> None:
    """Remove directories left empty (deepest first), keeping ``base`` itself."""
    if not base.is_dir():
        return
    for directory in sorted(
        (p for p in base.rglob('*') if p.is_dir() and not p.is_symlink()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()


def _prune_broken_links(rules_md: Path, counters: dict[str, int]) -> None:
    """
    Remove every broken symlink anywhere under ``rules_md`` (a link whose
    target no longer exists — e.g. an orphan left when a source file was
    renamed or moved), then prune the empty directories left behind.

    This runs tree-wide, so flat single-file links (rules_md/<name>.md) that
    have no batch directory to scope pruning to are still cleaned up. Real
    files and links with existing targets are left untouched.
    """
    if not rules_md.is_dir():
        return

    for path in sorted(
        (p for p in rules_md.rglob('*') if p.is_symlink()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        # A broken symlink: it is a link, but its target does not resolve.
        if not path.exists():
            path.unlink()
            print(f'  [prune] {path.relative_to(rules_md).as_posix()} — broken link (source moved/renamed)')
            counters['pruned'] += 1

    _prune_empty_dirs(rules_md)


def _iter_source_files(source: Path) -> list[tuple[Path, str]]:
    """
    Yield (absolute_source_file, relative_dest_path) pairs for every markdown
    file under a directory source, preserving its subfolder structure.
    """
    pairs: list[tuple[Path, str]] = []
    for path in sorted(p for p in source.rglob('*.md') if p.is_file()):
        pairs.append((path, path.relative_to(source).as_posix()))
    return pairs


def _place_link(
    dest: Path, target: Path, rel_display: str, counters: dict[str, int]
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.is_symlink():
        if Path(dest.readlink()).resolve() == target:
            counters['unchanged'] += 1  # already points at the right source
            return
        dest.unlink()
        dest.symlink_to(target)
        counters['refreshed'] += 1
        return

    if dest.exists():
        print(f'  [skip] {rel_display} exists and is host-owned — left untouched')
        counters['skipped'] += 1
        return

    dest.symlink_to(target)
    counters['created'] += 1


def _symlink_rules(repo_root: Path, source: Path, name: str) -> dict[str, int]:
    rules_md = repo_root.joinpath(*RULES_MD_REL)
    if not rules_md.parent.is_dir():
        raise SymlinkRulesError(
            f'Lore directory not found: {rules_md.parent}. Run install.sh first.'
        )

    counters = {'created': 0, 'refreshed': 0, 'unchanged': 0, 'skipped': 0, 'pruned': 0}

    # A single file links flat as rules_md/<name>.md — no wrapper directory.
    # A directory source becomes a batch dir rules_md/<name>/ mirroring its tree.
    if source.is_file():
        dest = rules_md / f'{name}.md'
        _place_link(dest, source, dest.relative_to(repo_root).as_posix(), counters)
    else:
        files = _iter_source_files(source)
        if not files:
            raise SymlinkRulesError(
                f'No markdown (.md) files found under source: {source}'
            )

        dest_base = rules_md / name
        linked: set[Path] = set()
        for src_file, rel in files:
            dest = dest_base / rel
            display = dest.relative_to(repo_root).as_posix()
            _place_link(dest, src_file, display, counters)
            linked.add(dest)

        # Drop this batch's stale symlinks (sources that vanished since the
        # last run); host-owned real files and current links are left intact.
        _prune_stale_links(dest_base, linked, counters)

    # Tree-wide sweep: clean up any orphaned broken links (e.g. flat single-file
    # links whose source was renamed) that batch-scoped pruning cannot catch.
    _prune_broken_links(rules_md, counters)

    return counters


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='agent-smith-symlink-rules',
        description=(
            'Symlink rules documentation (a folder with subfolders, or a single '
            '.md file) into .agent-smith/lore/rules_md/, namespaced per source.'
        ),
    )
    parser.add_argument(
        'source',
        help='Path to rules documentation: a directory (subfolders supported) '
        'or a single .md file.',
    )
    parser.add_argument(
        '--name',
        help='Batch name. For a directory source this is the subdirectory under '
        'rules_md/; for a single file it is the linked filename (rules_md/<name>.md). '
        'Default: the source folder name, or the file stem for a single .md.',
    )
    parser.add_argument(
        '--repo-root',
        help='Project root. Default: walk up from the current directory to the '
        'nearest .git. Non-git projects must pass this explicitly.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        repo_root = _resolve_repo_root(args.repo_root)

        if not (repo_root / '.agent-smith').is_dir():
            raise SymlinkRulesError(
                f'.agent-smith not found at {repo_root}. Run install.sh first.'
            )

        target, name_basis = _resolve_source(args.source)
        name = _resolve_name(target, name_basis, args.name)
        counters = _symlink_rules(repo_root, target, name)
    except SymlinkRulesError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1

    rules_md = repo_root.joinpath(*RULES_MD_REL)
    dest = rules_md / (f'{name}.md' if target.is_file() else name)
    print(
        f"""
Symlink rules summary
---------------------
Source: {target}
Batch: {name}
Destination: {dest.relative_to(repo_root).as_posix()}
Symlinks created: {counters['created']}, refreshed: {counters['refreshed']}, unchanged: {counters['unchanged']}, skipped: {counters['skipped']}, pruned: {counters['pruned']}"""
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
