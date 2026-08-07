from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_smith.scripts import symlink_rules


@pytest.fixture
def host(tmp_path: Path) -> Path:
    """Host repo with an initialized .agent-smith/lore/rules_md/ tree."""
    host = tmp_path / 'host'
    host.mkdir()
    subprocess.run(['git', 'init', '-q', '.'], cwd=host, check=True)
    (host / '.agent-smith' / 'lore' / 'rules_md').mkdir(parents=True)
    return host


def _rules_md(host: Path) -> Path:
    return host / '.agent-smith' / 'lore' / 'rules_md'


def _run(host: Path, source: Path, *args: str) -> int:
    return symlink_rules.main(['--repo-root', str(host), str(source), *args])


def _make_docs(root: Path) -> Path:
    """A source tree with nested markdown and a non-md file."""
    docs = root / 'docs'
    (docs / 'sub').mkdir(parents=True)
    (docs / 'top.md').write_text('# Top\n', encoding='utf-8')
    (docs / 'sub' / 'nested.md').write_text('# Nested\n', encoding='utf-8')
    (docs / 'ignore.txt').write_text('nope\n', encoding='utf-8')
    return docs


def test_single_md_file(host: Path, tmp_path: Path) -> None:
    src = tmp_path / 'GUIDE.md'
    src.write_text('# Guide\n', encoding='utf-8')

    assert _run(host, src) == 0

    # Single file links flat as rules_md/<name>.md — no wrapper directory.
    link = _rules_md(host) / 'GUIDE.md'
    assert link.is_symlink()
    assert link.is_file()
    assert not (_rules_md(host) / 'GUIDE').exists()
    assert Path(link.readlink()).is_absolute()
    assert Path(link.readlink()).resolve() == src.resolve()


def test_single_md_file_name_override(host: Path, tmp_path: Path) -> None:
    src = tmp_path / 'GUIDE.md'
    src.write_text('# Guide\n', encoding='utf-8')

    assert _run(host, src, '--name', 'onboarding') == 0

    link = _rules_md(host) / 'onboarding.md'
    assert link.is_symlink()
    assert Path(link.readlink()).resolve() == src.resolve()


def test_symlink_source_keeps_user_name_and_resolves_target(
    host: Path, tmp_path: Path
) -> None:
    """A source that is itself a symlink: name from what the user typed,
    link target resolved to the real file (not the intermediate link)."""
    real = tmp_path / 'tech-stack.md'
    real.write_text('# Tech\n', encoding='utf-8')
    alias = tmp_path / 'poooopoooo.md'
    alias.symlink_to(real)

    assert _run(host, alias) == 0

    # Named after the alias the user passed, not the resolved target.
    link = _rules_md(host) / 'poooopoooo.md'
    assert link.is_symlink()
    assert not (_rules_md(host) / 'tech-stack.md').exists()
    # Points at the real file, not the intermediate symlink.
    assert Path(link.readlink()).resolve() == real.resolve()
    assert Path(link.readlink()) == real.resolve()


def test_folder_tree_mirrored(host: Path, tmp_path: Path) -> None:
    docs = _make_docs(tmp_path)

    assert _run(host, docs) == 0

    base = _rules_md(host) / 'docs'
    top = base / 'top.md'
    nested = base / 'sub' / 'nested.md'
    assert top.is_symlink() and top.is_file()
    assert nested.is_symlink() and nested.is_file()
    # Non-markdown is ignored
    assert not (base / 'ignore.txt').exists()


def test_name_override(host: Path, tmp_path: Path) -> None:
    docs = _make_docs(tmp_path)

    assert _run(host, docs, '--name', 'custom') == 0

    assert (_rules_md(host) / 'custom' / 'top.md').is_symlink()
    assert not (_rules_md(host) / 'docs').exists()


def test_batches_accumulate(host: Path, tmp_path: Path) -> None:
    first = tmp_path / 'a.md'
    first.write_text('# A\n', encoding='utf-8')
    second = tmp_path / 'b.md'
    second.write_text('# B\n', encoding='utf-8')

    assert _run(host, first) == 0
    assert _run(host, second) == 0

    assert (_rules_md(host) / 'a.md').is_symlink()
    assert (_rules_md(host) / 'b.md').is_symlink()


def test_rerun_same_name_is_idempotent(host: Path, tmp_path: Path) -> None:
    docs = _make_docs(tmp_path)

    assert _run(host, docs) == 0
    assert _run(host, docs) == 0

    base = _rules_md(host) / 'docs'
    links = sorted(p for p in base.rglob('*.md'))
    assert len(links) == 2
    assert all(p.is_symlink() for p in links)


def test_rerun_same_name_reports_unchanged(host: Path, tmp_path: Path) -> None:
    docs = _make_docs(tmp_path)
    assert symlink_rules.main(['--repo-root', str(host), str(docs)]) == 0

    counters = symlink_rules._symlink_rules(host, docs.resolve(), 'docs')
    assert counters['created'] == 0
    assert counters['unchanged'] == 2
    assert counters['pruned'] == 0


def test_rerun_reflects_removed_source_files(host: Path, tmp_path: Path) -> None:
    docs = _make_docs(tmp_path)
    assert _run(host, docs) == 0

    # Drop a source file, then re-run: stale link must be gone.
    (docs / 'sub' / 'nested.md').unlink()
    assert _run(host, docs) == 0

    base = _rules_md(host) / 'docs'
    assert (base / 'top.md').is_symlink()
    assert not (base / 'sub' / 'nested.md').exists()
    # Empty subdir pruned
    assert not (base / 'sub').exists()


def test_prune_counter_on_removed_source(host: Path, tmp_path: Path) -> None:
    docs = _make_docs(tmp_path)
    assert symlink_rules.main(['--repo-root', str(host), str(docs)]) == 0

    (docs / 'sub' / 'nested.md').unlink()
    counters = symlink_rules._symlink_rules(host, docs.resolve(), 'docs')
    assert counters['pruned'] == 1
    assert counters['unchanged'] == 1


def test_renamed_single_file_prunes_orphaned_link(host: Path, tmp_path: Path) -> None:
    """Renaming a single-file source and re-linking must not leave a broken
    orphan link under its old name (no batch dir to scope pruning to)."""
    old = tmp_path / 'poooopoooo.md'
    old.write_text('# Doc\n', encoding='utf-8')
    assert _run(host, old) == 0
    assert (_rules_md(host) / 'poooopoooo.md').is_symlink()

    # Rename the source and link again under the new name.
    new = tmp_path / 'toootoooo.md'
    old.rename(new)
    assert _run(host, new) == 0

    new_link = _rules_md(host) / 'toootoooo.md'
    old_link = _rules_md(host) / 'poooopoooo.md'
    assert new_link.is_symlink() and new_link.is_file()
    # The orphaned old link is gone (was broken after the rename).
    assert not old_link.is_symlink()
    assert not old_link.exists()


def test_broken_link_from_external_move_is_swept(host: Path, tmp_path: Path) -> None:
    """A broken managed link is cleaned up on the next run even when the
    current source is unrelated."""
    gone = tmp_path / 'gone.md'
    gone.write_text('# Gone\n', encoding='utf-8')
    assert _run(host, gone) == 0
    gone.unlink()  # source disappears; link now dangles

    other = tmp_path / 'other.md'
    other.write_text('# Other\n', encoding='utf-8')
    counters = symlink_rules._symlink_rules(host, other.resolve(), 'other')

    assert counters['pruned'] == 1
    assert not (_rules_md(host) / 'gone.md').exists()
    assert (_rules_md(host) / 'other.md').is_symlink()


def test_real_host_file_collision_is_skipped(host: Path, tmp_path: Path, capsys) -> None:
    docs = _make_docs(tmp_path)
    base = _rules_md(host) / 'docs'
    base.mkdir(parents=True)
    real = base / 'top.md'
    real.write_text('# Host authored\n', encoding='utf-8')

    assert _run(host, docs) == 0
    out = capsys.readouterr().out

    assert '[skip]' in out
    assert not real.is_symlink()
    assert real.read_text() == '# Host authored\n'
    # The non-colliding nested file still links
    assert (base / 'sub' / 'nested.md').is_symlink()


def test_missing_source_errors(host: Path, tmp_path: Path, capsys) -> None:
    assert _run(host, tmp_path / 'nope') == 1
    err = capsys.readouterr().err
    assert 'Source path does not exist' in err


def test_non_md_single_file_errors(host: Path, tmp_path: Path, capsys) -> None:
    src = tmp_path / 'notes.txt'
    src.write_text('x\n', encoding='utf-8')
    assert _run(host, src) == 1
    assert 'must be a markdown' in capsys.readouterr().err


def test_empty_folder_errors(host: Path, tmp_path: Path, capsys) -> None:
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert _run(host, empty) == 1
    assert 'No markdown' in capsys.readouterr().err


def test_missing_agent_smith_errors(tmp_path: Path, capsys) -> None:
    bare = tmp_path / 'bare'
    bare.mkdir()
    subprocess.run(['git', 'init', '-q', '.'], cwd=bare, check=True)
    src = tmp_path / 'a.md'
    src.write_text('# A\n', encoding='utf-8')
    assert symlink_rules.main(['--repo-root', str(bare), str(src)]) == 1
    assert '.agent-smith not found' in capsys.readouterr().err


def test_invalid_name_errors(host: Path, tmp_path: Path, capsys) -> None:
    src = tmp_path / 'a.md'
    src.write_text('# A\n', encoding='utf-8')
    assert _run(host, src, '--name', 'a/b') == 1
    assert 'Invalid batch name' in capsys.readouterr().err
