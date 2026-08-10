"""Install and remove versioned Agent Smith releases."""

from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from agent_smith.scripts import sync

GITHUB_ORG = 'GektorPrime'
GITHUB_REPO = 'agent-smith'
GIT_URL = f'https://github.com/{GITHUB_ORG}/{GITHUB_REPO}.git'
VERSION_PATTERN = re.compile(r'\d+\.\d+\.\d+')


class InstallError(RuntimeError):
    pass


def _run_git(*args: str, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ['git', *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
        )
    except FileNotFoundError as exc:
        raise InstallError("'git' is required and was not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or 'git command failed'
        raise InstallError(detail) from exc
    return result.stdout.strip()


def _source_checkout() -> Path | None:
    value = os.getenv('AGENT_SMITH_SOURCE_DIR')
    if not value:
        return None
    source = Path(value).expanduser().resolve()
    if not (source / 'wireframe').is_dir() or not (source / '.git').exists():
        raise InstallError(f'AGENT_SMITH_SOURCE_DIR is not an Agent Smith checkout: {source}')
    return source


def _latest_remote_tag() -> str:
    output = _run_git('ls-remote', '--tags', GIT_URL)
    tags = {
        line.split('refs/tags/', 1)[1].removesuffix('^{}')
        for line in output.splitlines()
        if 'refs/tags/' in line
    }
    tags = {tag for tag in tags if re.fullmatch(r'\d+\.\d+\.\d+', tag)}
    if not tags:
        raise InstallError(f'No release tags found in {GIT_URL}')
    return max(tags, key=lambda tag: tuple(int(part) for part in tag.split('.')))


def _resolve_version(requested: str | None, source: Path | None) -> str:
    if requested:
        version = requested
    elif source:
        version = _run_git('describe', '--tags', '--abbrev=0', cwd=source)
        if not version:
            raise InstallError(
                f'No git tags found in {source}. Run git fetch --tags or pass --version.'
            )
    else:
        print('Resolving latest release from remote...')
        version = _latest_remote_tag()
        print(f'Latest release: {version}')

    if not VERSION_PATTERN.fullmatch(version):
        raise InstallError(
            f"Invalid release version '{version}'; expected MAJOR.MINOR.PATCH"
        )
    return version


def _resolve_repo_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_dir():
            raise InstallError(f'--repo-root does not exist: {root}')
        return root.resolve()

    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / '.git').exists():
            return candidate.resolve()
    raise InstallError(
        'Not inside a git repository. Change into the project or pass '
        '--repo-root <path>.'
    )


def _acquire_release(version: str, destination: Path, source: Path | None) -> None:
    if source:
        try:
            _run_git('rev-parse', '--verify', f'refs/tags/{version}', cwd=source)
        except InstallError:
            pass
        else:
            print(f'Acquiring release: local tag {version}')
            archive = destination.parent / 'source.tar'
            _run_git(
                'archive', '--format=tar', f'--output={archive}', f'refs/tags/{version}',
                cwd=source,
            )
            with tarfile.open(archive, 'r') as tar:
                tar.extractall(destination, filter='data')
            return

    print(f'Acquiring release {version} via git')
    _run_git('init', '--quiet', str(destination))
    _run_git('remote', 'add', 'origin', GIT_URL, cwd=destination)
    _run_git('fetch', '--quiet', '--depth', '1', 'origin', f'refs/tags/{version}', cwd=destination)
    _run_git('checkout', '--quiet', '--detach', 'FETCH_HEAD', cwd=destination)
    shutil.rmtree(destination / '.git', ignore_errors=True)


def _install_release(version: str, install_base: Path, source: Path | None) -> Path:
    install_dir = install_base / version
    install_base.mkdir(parents=True, exist_ok=True)
    if install_dir.is_dir():
        print(f'v{version} already installed - skipping extraction')
        return install_dir

    with tempfile.TemporaryDirectory(
        prefix=f'.tmp-install-{version}.', dir=install_base
    ) as tmp:
        staged = Path(tmp) / version
        staged.mkdir()
        _acquire_release(version, staged, source)
        staged.replace(install_dir)
    return install_dir


def _seed_wireframe(install_dir: Path, install_base: Path, repo_root: Path) -> None:
    source = install_dir / 'wireframe'
    destination = repo_root / '.agent-smith'
    if not source.is_dir():
        raise InstallError(f'Wireframe source not found in release: {source}')

    for path in sorted(item for item in source.rglob('*') if item.is_file()):
        relative = path.relative_to(source)
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            shutil.copy2(path, output)
            continue
        if filecmp.cmp(path, output, shallow=False):
            print(f'  [skip] {relative.as_posix()} already current')
            continue

        managed = any(
            candidate != path
            and candidate.is_file()
            and filecmp.cmp(candidate, output, shallow=False)
            for candidate in install_base.glob(f'*/wireframe/{relative.as_posix()}')
        )
        if managed:
            shutil.copy2(path, output)
            print(f'  [update] {relative.as_posix()} refreshed from release')
        else:
            print(f'  [skip] {relative.as_posix()} exists and is host-owned')

    tmp = destination / 'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / '.gitkeep').touch(exist_ok=True)


def _refresh_persistent_cli(install_dir: Path) -> str:
    """Re-point the persistent `agent-smith-*` PATH commands at install_dir.

    `install.sh` bootstraps the commands with `uv tool install`, but the
    console installer historically did not, so the PATH commands stayed pinned
    to whatever was last bootstrapped while `~/.agent-smith-tool/<ver>` moved
    ahead. Running `uv tool install --force <install_dir>` keeps the bare
    commands matching the version just installed. Degrades gracefully: a
    missing or failing `uv` warns but never fails the release install.
    """
    if shutil.which('uv') is None:
        print("  [skip] 'uv' not found — persistent CLI not refreshed")
        return 'skipped (uv not found)'

    try:
        subprocess.run(
            ['uv', 'tool', 'install', '--force', str(install_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or 'uv tool install failed'
        print(f'  [warn] persistent CLI refresh failed: {detail}')
        return 'failed'

    return 'refreshed'


def _uninstall(
    version: str, install_base: Path, repo_root: Path | None, purge_host_data: bool
) -> None:
    install_dir = install_base / version
    if install_dir.is_dir():
        shutil.rmtree(install_dir)
        print(f'Removed {install_dir}')
    else:
        print(f'Nothing to remove at {install_dir}')

    if purge_host_data:
        assert repo_root is not None
        host_data = repo_root / '.agent-smith'
        # Deactivate the project first so the .opencode symlinks and config
        # entries pointing into .agent-smith are removed while it still exists.
        # Abort before deleting .agent-smith if desync fails, so nothing is
        # left half-nuked.
        try:
            sync._desync(repo_root)
        except sync.SyncError as exc:
            raise InstallError(str(exc)) from exc

        if host_data.is_dir():
            shutil.rmtree(host_data)
            print(f'Purged host data: {host_data}')
        else:
            print(f'No host data to purge at {host_data}')
    else:
        print('Host data not purged (pass --purge-host-data to remove it)')


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='agent-smith-install-release',
        description='Install or remove a versioned Agent Smith release.',
    )
    parser.add_argument(
        '--version', default=os.getenv('AGENT_SMITH_VERSION'), help='Release tag (default: latest)'
    )
    parser.add_argument(
        '--repo-root',
        default=os.getenv('AGENT_SMITH_TARGET_REPO_ROOT'),
        help='Host project root (default: nearest Git repository)',
    )
    parser.add_argument('--uninstall', action='store_true', help='Remove the selected release')
    parser.add_argument(
        '--purge-host-data',
        action='store_true',
        help='Also remove <repo-root>/.agent-smith during uninstall',
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.purge_host_data and not args.uninstall:
        parser.error('--purge-host-data can only be used with --uninstall')

    try:
        source = _source_checkout()
        version = _resolve_version(args.version, source)
        install_base = Path.home() / '.agent-smith-tool'

        if args.uninstall:
            repo_root = _resolve_repo_root(args.repo_root) if args.purge_host_data else None
            _uninstall(version, install_base, repo_root, args.purge_host_data)
            return

        repo_root = _resolve_repo_root(args.repo_root)
        install_dir = _install_release(version, install_base, source)
        _seed_wireframe(install_dir, install_base, repo_root)
        cli_status = _refresh_persistent_cli(install_dir)
        if cli_status == 'refreshed':
            next_step = f'agent-smith-sync --repo-root "{repo_root}"'
        else:
            # Bare command may be stale/absent; steer to the pinned invocation.
            next_step = (
                f'uvx --from "{install_dir}" agent-smith-sync '
                f'--repo-root "{repo_root}"'
            )
        print(
            f'\nInstall summary\n---------------\nInstalled version: {version}\n'
            f'.agent-smith path: {repo_root / ".agent-smith"}\n'
            f'Persistent CLI: {cli_status}\n'
            f'Next step: {next_step}'
        )
    except InstallError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()
