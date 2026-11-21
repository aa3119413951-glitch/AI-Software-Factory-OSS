#!/usr/bin/env python3
"""Local opt-in private Office archive for user-supplied Markdown exports.

No browser control, network I/O, remote upload, grant creation or tenure change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone

MAX_BYTES = 8 * 1024 * 1024
SLUG = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$')
SENSITIVE = re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{12,}|ghp_[A-Za-z0-9]{12,}|\bBearer\s+[A-Za-z0-9_.-]{16,}', re.IGNORECASE)

class ArchiveError(ValueError):
    pass


def _slug(value: str, label: str) -> str:
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise ArchiveError(f'invalid {label} identifier')
    return value


def _safe_directory(path: Path, *, must_exist: bool = True) -> Path:
    path = Path(os.path.abspath(path))
    cur = path
    while True:
        if cur.is_symlink():
            raise ArchiveError('symlink path is forbidden')
        if cur.parent == cur:
            break
        cur = cur.parent
    if must_exist and (not path.is_dir()):
        raise ArchiveError('required directory missing')
    return path


def _safe_regular(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    _safe_directory(path.parent)
    if path.is_symlink() or not path.is_file():
        raise ArchiveError('file is absent, not regular, or a symlink')
    return path


def _read_json_file(path: Path) -> dict:
    _safe_regular(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise ArchiveError('invalid archive metadata') from exc
    if not isinstance(data, dict):
        raise ArchiveError('invalid archive metadata')
    return data


def _read_export(source: Path) -> bytes:
    source = _safe_regular(source)
    if source.suffix.lower() != '.md':
        raise ArchiveError('export must be Markdown .md')
    if source.stat().st_size > MAX_BYTES:
        raise ArchiveError('export too large; split with explicit scope')
    data = source.read_bytes()
    if not data.strip() or b'\x00' in data:
        raise ArchiveError('empty or binary Markdown export')
    try:
        data.decode('utf-8')
    except UnicodeError as exc:
        raise ArchiveError('export not UTF-8') from exc
    if SENSITIVE.search(data):
        raise ArchiveError('high-risk credential pattern found; inspect and remove before archiving')
    return data


def _office(root: Path, role: str) -> Path:
    root = _safe_directory(root)
    role = _slug(role, 'role')
    return _safe_directory(root / 'offices' / role)


def _guard_private(root: Path, role: str, approval_ref: str) -> None:
    if not isinstance(approval_ref, str) or not approval_ref.strip():
        raise ArchiveError('Human approval reference required; command is not proof of authority')
    root = _safe_directory(root)
    marker = _read_json_file(root / '.private-conversation-archive.json')
    approved_roles = marker.get('approved_roles')
    if (marker.get('private_archive') is not True or
            marker.get('enabled') is not True or
            not isinstance(approved_roles, list) or
            role not in approved_roles):
        raise ArchiveError('private archive not explicitly enabled for this role')
    # Known public OSS source must never receive private transcripts.
    gitconfig = root / '.git' / 'config'
    if gitconfig.exists():
        content = _safe_regular(gitconfig).read_text(encoding='utf-8', errors='replace').lower()
        if 'ai-software-factory-oss' in content:
            raise ArchiveError('public OSS repository remote is not a private archive destination')


def preflight(root: Path, role: str) -> dict:
    office = _office(root, role)
    current = office / 'desk' / 'CURRENT.md'
    if not current.is_file() or current.is_symlink() or (office / 'desk').is_symlink() or not current.read_text(encoding='utf-8').strip():
        return {'status': 'BLOCKED_MISSING_CURRENT', 'next': 'Update current recovery cursor first; no fabricated predecessor.'}
    archive = office / 'conversation-archive'
    if archive.exists():
        _safe_directory(archive)
    marker = Path(root) / '.private-conversation-archive.json'
    if not marker.exists():
        return {'status': 'HUMAN_ACTION_REQUIRED', 'next': 'Human must verify a private archive destination and explicitly enable it.'}
    return {'status': 'READY_TO_IMPORT', 'next': 'Obtain an explicitly approved Markdown export and archive it locally.'}


def _atomic_json(path: Path, content: dict) -> None:
    _safe_directory(path.parent)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.pending-', suffix='.json', delete=False) as h:
            temp = Path(h.name)
            json.dump(content, h, ensure_ascii=False, indent=2, sort_keys=True)
            h.write('\n')
            h.flush()
            os.fsync(h.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def _write_index(archive: Path, role: str) -> None:
    lines = [f'# Conversation Archive — {role}', '',
             'Private historical context only. CURRENT, authority and fresh evidence take precedence.', '',
             '| Tenure | Session | Scope | Coverage | SHA-256 |',
             '| --- | --- | --- | --- | --- |']
    tenures = archive / 'tenures'
    if tenures.exists():
        for directory in sorted(tenures.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                raise ArchiveError('unrecognized tenure entry')
            manifest_path = directory / 'archive-manifest.json'
            if not manifest_path.exists():
                raise ArchiveError('incomplete tenure manifest')
            manifest = _read_json_file(manifest_path)
            if manifest.get('role') != role or manifest.get('tenure') != directory.name:
                raise ArchiveError('manifest identity mismatch')
            for e in manifest.get('entries', []):
                session = _slug(e['session'], 'session')
                lines.append(f"| {directory.name} | {session} | {e['scope']} | {e['coverage']} | `{e['sha256']}` |")
    dest = archive / 'INDEX.md'
    temp = archive / '.INDEX.md.pending'
    if temp.is_symlink() or dest.is_symlink():
        raise ArchiveError('index symlink forbidden')
    temp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    temp.replace(dest)


def preview_session(*, root: Path, role: str, tenure: str, session: str,
                    source: Path, approval_ref: str, export_scope: str) -> dict:
    """Validate the intended import without writing any file or directory."""
    role = _slug(role, 'role')
    tenure = _slug(tenure, 'tenure')
    session = _slug(session, 'session')
    if export_scope != 'available-active-branch':
        raise ArchiveError('unsupported export scope')
    _guard_private(root, role, approval_ref)
    if preflight(root, role)['status'] != 'READY_TO_IMPORT':
        raise ArchiveError('recovery cursor or archive preflight not ready')
    payload = _read_export(source)
    digest = hashlib.sha256(payload).hexdigest()
    target = _office(root, role) / 'conversation-archive' / 'tenures' / tenure / 'sessions' / f'{session}.md'
    if target.exists():
        existing = _safe_regular(target)
        if hashlib.sha256(existing.read_bytes()).hexdigest() != digest:
            raise ArchiveError('existing session ID has different contents')
    return {'status': 'DRY_RUN_READY', 'session': session, 'sha256': digest,
            'bytes': len(payload), 'next': 'Approval and --apply required for local copy; this is not a cloud upload.'}

def import_session(*, root: Path, role: str, tenure: str, session: str,
                   source: Path, approval_ref: str, export_scope: str,
                   complete: bool = False) -> dict:
    role = _slug(role, 'role')
    tenure = _slug(tenure, 'tenure')
    session = _slug(session, 'session')
    if export_scope != 'available-active-branch':
        raise ArchiveError('only available-active-branch export scope is supported')
    if type(complete) is not bool:
        raise ArchiveError('complete must be boolean')
    root = _safe_directory(root)
    office = _office(root, role)
    _guard_private(root, role, approval_ref)
    check = preflight(root, role)
    if check['status'] != 'READY_TO_IMPORT':
        raise ArchiveError(check['status'])
    payload = _read_export(source)
    digest = hashlib.sha256(payload).hexdigest()
    archive = office / 'conversation-archive'
    tenure_dir = archive / 'tenures' / tenure
    session_dir = tenure_dir / 'sessions'
    for path in [archive, archive / 'tenures', tenure_dir, session_dir]:
        if path.exists():
            _safe_directory(path)
        else:
            path.mkdir(mode=0o700)
    manifest_path = tenure_dir / 'archive-manifest.json'
    manifest = _read_json_file(manifest_path) if manifest_path.exists() else {
        'schema_version': 1, 'role': role, 'tenure': tenure, 'entries': []}
    if manifest.get('role') != role or manifest.get('tenure') != tenure or not isinstance(manifest.get('entries'), list):
        raise ArchiveError('manifest identity mismatch')
    dest = session_dir / f'{session}.md'
    existing = next((e for e in manifest['entries'] if e.get('session') == session), None)
    if existing:
        if existing.get('sha256') != digest:
            raise ArchiveError('same session ID with different content; do not overwrite')
        if not dest.is_file() or dest.is_symlink() or hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            raise ArchiveError('existing archived file missing or altered')
        _write_index(archive, role)
        return {'status': 'ALREADY_ARCHIVED', 'role': role, 'session': session, 'sha256': digest,
                'coverage': existing['coverage']}
    if dest.exists() or dest.is_symlink():
        if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            raise ArchiveError('archive file exists with different content')
        # Recover a prior interrupted manifest write, but preserve the verified file.
    else:
        with dest.open('xb') as h:
            h.write(payload)
            h.flush()
            os.fsync(h.fileno())
        dest.chmod(0o600)
    if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
        raise ArchiveError('archive readback checksum mismatch')
    coverage = 'CONFIRMED_AVAILABLE_BRANCH_ONLY' if complete else 'PARTIAL_OR_UNKNOWN'
    manifest['entries'].append({
        'session': session, 'file': f'sessions/{session}.md', 'sha256': digest,
        'bytes': len(payload), 'scope': export_scope, 'coverage': coverage,
        'source_platform': 'ChatGPT', 'export_method': 'manual-md',
        'approval_reference': approval_ref,
        'archived_at_utc': datetime.now(timezone.utc).isoformat(),
    })
    _atomic_json(manifest_path, manifest)
    _write_index(archive, role)
    if verify_archive(root, role)['status'] != 'VERIFIED':
        raise ArchiveError('post-write verification failed')
    return {'status': 'ARCHIVED', 'role': role, 'session': session,
            'coverage': coverage, 'sha256': digest}


def verify_archive(root: Path, role: str) -> dict:
    role = _slug(role, 'role')
    office = _office(root, role)
    archive = office / 'conversation-archive'
    if not archive.exists():
        return {'status': 'NO_ARCHIVE', 'sessions': 0}
    _safe_directory(archive)
    tenures = archive / 'tenures'
    if not tenures.is_dir() or tenures.is_symlink():
        return {'status': 'TAMPERED', 'sessions': 0, 'reason': 'missing tenures'}
    count = 0
    try:
        for tenure_dir in sorted(tenures.iterdir()):
            _safe_directory(tenure_dir)
            manifest = _read_json_file(tenure_dir / 'archive-manifest.json')
            if (manifest.get('role') != role or manifest.get('tenure') != tenure_dir.name or
                    not isinstance(manifest.get('entries'), list)):
                raise ArchiveError('manifest identity mismatch')
            seen = set()
            for entry in manifest['entries']:
                session = _slug(entry['session'], 'session')
                if session in seen or entry.get('file') != f'sessions/{session}.md':
                    raise ArchiveError('duplicate or malformed entry')
                seen.add(session)
                stored = _safe_regular(tenure_dir / 'sessions' / f'{session}.md')
                payload = stored.read_bytes()
                if (len(payload) != entry['bytes'] or
                        hashlib.sha256(payload).hexdigest() != entry['sha256']):
                    raise ArchiveError('checksum mismatch')
                count += 1
    except (ArchiveError, KeyError, TypeError, OSError, ValueError) as exc:
        return {'status': 'TAMPERED', 'sessions': count, 'reason': str(exc)}
    return {'status': 'VERIFIED', 'sessions': count,
            'note': 'Local integrity only; no proof of authority, completeness or remote upload.'}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for cmd in ('check', 'import', 'verify'):
        x = sub.add_parser(cmd)
        x.add_argument('--root', type=Path, required=True)
        x.add_argument('--role', required=True)
        if cmd == 'import':
            x.add_argument('--tenure', required=True)
            x.add_argument('--session', required=True)
            x.add_argument('--source', type=Path, required=True)
            x.add_argument('--approval-ref', required=True)
            x.add_argument('--scope', default='available-active-branch')
            x.add_argument('--confirm-available-branch-complete', action='store_true')
            x.add_argument('--apply', action='store_true')
    args = p.parse_args()
    try:
        if args.command == 'check':
            result = preflight(args.root, args.role)
        elif args.command == 'verify':
            result = verify_archive(args.root, args.role)
        elif not args.apply:
            result = preview_session(root=args.root, role=args.role, tenure=args.tenure,
                session=args.session, source=args.source, approval_ref=args.approval_ref,
                export_scope=args.scope)
        else:
            result = import_session(root=args.root, role=args.role, tenure=args.tenure,
                session=args.session, source=args.source, approval_ref=args.approval_ref,
                export_scope=args.scope, complete=args.confirm_available_branch_complete)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] in {'READY_TO_IMPORT', 'VERIFIED', 'NO_ARCHIVE',
                    'DRY_RUN_READY', 'ARCHIVED', 'ALREADY_ARCHIVED'} else 3
    except (ArchiveError, OSError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3

if __name__ == '__main__':
    raise SystemExit(main())
