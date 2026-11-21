import importlib.util
import json
import pathlib
import tempfile
import unittest

MODULE = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'conversation_archive.py'
spec = importlib.util.spec_from_file_location('conversation_archive', MODULE)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / 'private-instance'
        self.root.mkdir()
        self.office = self.root / 'offices' / 'management'
        (self.office / 'desk').mkdir(parents=True)
        (self.office / 'desk' / 'CURRENT.md').write_text('# CURRENT\nWork: issue #1\nNext: review\n', encoding='utf-8')
        self.source = pathlib.Path(self.tmp.name) / 'export.md'
        self.source.write_text('# Synthetic conversation\nHello, factory!\n', encoding='utf-8')
        self.marker = self.root / '.private-conversation-archive.json'
        self.marker.write_text(json.dumps({'private_archive': True, 'enabled': True, 'approved_roles': ['management']}), encoding='utf-8')
    def opts(self):
        return dict(root=self.root, role='management', tenure='term-01', session='chat-01', source=self.source, approval_ref='Human decision 2026-11-21', export_scope='available-active-branch', complete=False)
    def test_import_and_verify(self):
        result = archive.import_session(**self.opts())
        self.assertEqual(result['status'], 'ARCHIVED')
        self.assertEqual(result['coverage'], 'PARTIAL_OR_UNKNOWN')
        self.assertEqual(archive.verify_archive(self.root, 'management')['status'], 'VERIFIED')
        self.assertIn('chat-01', (self.office / 'conversation-archive' / 'INDEX.md').read_text())
    def test_idempotency(self):
        first = archive.import_session(**self.opts())
        second = archive.import_session(**self.opts())
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertEqual(second['status'], 'ALREADY_ARCHIVED')
    def test_conflict_preserves_original(self):
        archive.import_session(**self.opts())
        self.source.write_text('# Synthetic conversation\nChanged', encoding='utf-8')
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
        self.assertEqual(archive.verify_archive(self.root, 'management')['status'], 'VERIFIED')
    def test_tamper_detected(self):
        archive.import_session(**self.opts())
        archived = self.office / 'conversation-archive' / 'tenures' / 'term-01' / 'sessions' / 'chat-01.md'
        archived.write_text('changed', encoding='utf-8')
        self.assertEqual(archive.verify_archive(self.root, 'management')['status'], 'TAMPERED')
    def test_no_authorization(self):
        self.marker.unlink()
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
        self.assertFalse((self.office / 'conversation-archive').exists())
    def test_empty_or_nonmarkdown_export(self):
        self.source.write_bytes(b'')
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
        self.source.write_bytes(b'\x00binary')
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
    def test_slug_path_traversal(self):
        with self.assertRaises(archive.ArchiveError): archive.import_session(**{**self.opts(), 'role':'../../bad'})
    def test_cross_role_not_authorized(self):
        (self.root / 'offices' / 'developer').mkdir()
        with self.assertRaises(archive.ArchiveError): archive.import_session(**{**self.opts(), 'role':'developer'})
    def test_symlink_export_rejected(self):
        target = pathlib.Path(self.tmp.name) / 'other.md'
        target.write_text('data')
        self.source.unlink()
        try: self.source.symlink_to(target)
        except OSError: self.skipTest('symlinks unavailable')
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
    def test_symlink_archive_rejected(self):
        (self.office / 'conversation-archive').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
    def test_public_git_remote_rejected(self):
        git = self.root / '.git'
        git.mkdir()
        (git / 'config').write_text('[remote "origin"]\n url = https://github.com/AAAlcest/AI-Software-Factory-OSS.git\n')
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
    def test_public_fork_git_remote_rejected(self):
        git = self.root / '.git'
        git.mkdir()
        (git / 'config').write_text('[remote "origin"]\n url = https://github.com/synthetic-developer/AI-Software-Factory-OSS.git\n')
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())

    def test_complete_only_explicit_and_scope_limited(self):
        x = archive.import_session(**{**self.opts(), 'complete':True})
        self.assertEqual(x['coverage'], 'CONFIRMED_AVAILABLE_BRANCH_ONLY')
    def test_unprepared_office_not_ready(self):
        (self.office / 'desk' / 'CURRENT.md').unlink()
        self.assertEqual(archive.preflight(self.root,'management')['status'], 'BLOCKED_MISSING_CURRENT')
    def test_no_true_succession_created(self):
        archive.import_session(**self.opts())
        self.assertFalse((self.office / 'handoffs').exists())
    def test_verify_missing_manifest(self):
        self.assertEqual(archive.verify_archive(self.root,'management')['status'], 'NO_ARCHIVE')

if __name__ == '__main__': unittest.main()

class MoreArchiveTests(unittest.TestCase):
    setUp = ArchiveTests.setUp
    opts = ArchiveTests.opts
    def test_preview_has_no_side_effects(self):
        result = archive.preview_session(**{k:v for k,v in self.opts().items() if k!='complete'})
        self.assertEqual(result['status'], 'DRY_RUN_READY')
        self.assertFalse((self.office / 'conversation-archive').exists())
    def test_preview_conflicting_existing_session(self):
        archive.import_session(**self.opts())
        self.source.write_text('changed export', encoding='utf-8')
        with self.assertRaises(archive.ArchiveError):
            archive.preview_session(**{k:v for k,v in self.opts().items() if k!='complete'})
    def test_sensitive_credentials_blocked(self):
        self.source.write_text('-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----', encoding='utf-8')
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
    def test_invalid_marker_role_type(self):
        self.marker.write_text(json.dumps({'private_archive': True, 'enabled': True, 'approved_roles': 'management'}))
        with self.assertRaises(archive.ArchiveError): archive.import_session(**self.opts())
    def test_symlink_current_parent_rejected(self):
        (self.office / 'desk' / 'CURRENT.md').unlink()
        (self.office / 'desk').rmdir()
        fake = pathlib.Path(self.tmp.name) / 'somewhere'
        fake.mkdir()
        (fake / 'CURRENT.md').write_text('copy')
        try: (self.office / 'desk').symlink_to(fake, target_is_directory=True)
        except OSError: self.skipTest('symlinks unavailable')
        self.assertEqual(archive.preflight(self.root, 'management')['status'], 'BLOCKED_MISSING_CURRENT')
