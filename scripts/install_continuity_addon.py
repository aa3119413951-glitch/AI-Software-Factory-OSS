#!/usr/bin/env python3
"""Copy public-safe continuity add-on files into a clean local OSS feature branch.

Dry-run by default. Never commits, pushes, creates PRs, configures permissions,
collects chat data, or operates on remote GitHub.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

PUBLIC_FILES = (
    'scripts/continuity_watch.py',
    'scripts/conversation_archive.py',
    'scripts/install_continuity_addon.py',
    'tests/test_continuity_watch.py',
    'tests/test_conversation_archive.py',
    'tests/test_install_continuity_addon.py',
    'docs/CONVERSATION_CONTINUITY.md',
    'skills/conversation-archive/SKILL.md',
    'templates/CONVERSATION_ARCHIVE_INDEX.md',
    'templates/CONVERSATION_ARCHIVE_MANIFEST.json',
    'templates/SESSION_HANDOVER_SUMMARY.md',
    'templates/PRIVATE_ARCHIVE_PERMISSION.example.json',
)


class InstallError(ValueError):
    pass


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(['git', *args], cwd=repo, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=15)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise InstallError('Git checkout or branch verification failed') from exc
    return result.stdout.strip()


def _assert_no_symlink(path: Path) -> None:
    current = path.absolute()
    while True:
        if current.is_symlink():
            raise InstallError('symlink paths are forbidden')
        if current.parent == current:
            break
        current = current.parent


def install_into_repo(repo: Path, addon: Path, *, apply: bool = False) -> dict:
    repo, addon = Path(repo).absolute(), Path(addon).absolute()
    _assert_no_symlink(repo)
    _assert_no_symlink(addon)
    if not repo.is_dir() or not addon.is_dir():
        raise InstallError('repository and addon directories must exist')
    if Path(_git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo.resolve():
        raise InstallError('repository must be a Git checkout root')
    branch = _git(repo, 'branch', '--show-current')
    if not branch or branch in {'main', 'master'}:
        raise InstallError('select an isolated feature branch before installing')
    if _git(repo, 'status', '--porcelain'):
        raise InstallError('refusing installation into a dirty checkout')
    for name in ('AGENTS.md', 'AI_ENTRYPOINT.md', 'README.md',
                 'docs/HANDOFF_AND_SUCCESSION.md', 'scripts/check_all.py'):
        path = repo / name
        _assert_no_symlink(path)
        if not path.is_file():
            raise InstallError('target does not have the expected OSS entrypoints')
    if 'AI Software Factory OSS' not in (repo / 'README.md').read_text(encoding='utf-8'):
        raise InstallError('target repository identity mismatch')
    for name in PUBLIC_FILES:
        src, dst = addon / name, repo / name
        _assert_no_symlink(src)
        _assert_no_symlink(dst)
        if not src.is_file():
            raise InstallError(f'missing packaged public source: {name}')
        if dst.exists():
            raise InstallError(f'refusing to overwrite existing file: {name}')
    if not apply:
        return {'status': 'DRY_RUN_READY', 'branch': branch, 'files': list(PUBLIC_FILES),
                'remote_write': False}
    for name in PUBLIC_FILES:
        src, dst = addon / name, repo / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        # x mode prevents accidental overwrite in a later race; no hidden fallback.
        with dst.open('xb') as output:
            output.write(src.read_bytes())
    return {'status': 'INSTALLED_LOCALLY', 'branch': branch, 'files': list(PUBLIC_FILES),
            'remote_write': False, 'next': 'Run unit tests and the upstream check_all.py, review diff, then create a PR using authorized credentials.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path, help='Existing clean OSS feature-branch checkout')
    parser.add_argument('--addon', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Copy public source files locally; never push')
    args = parser.parse_args()
    try:
        result = install_into_repo(args.repo, args.addon, apply=args.apply)
    except (InstallError, OSError, UnicodeError) as exc:
        result = {'status': 'BLOCKED', 'reason': str(exc)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result['status'] in {'DRY_RUN_READY', 'INSTALLED_LOCALLY'} else 3


if __name__ == '__main__':
    sys.exit(main())
