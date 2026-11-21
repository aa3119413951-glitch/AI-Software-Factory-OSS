"""Executable installation checks using a disposable local git repository."""
import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

ADDON = pathlib.Path(__file__).resolve().parents[1]
MODULE = ADDON / 'scripts' / 'install_continuity_addon.py'
spec = importlib.util.spec_from_file_location('install_continuity_addon', MODULE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name) / 'oss'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', 'feature/continuity-archive')
        git(self.repo, 'config', 'user.name', 'Synthetic Reviewer')
        git(self.repo, 'config', 'user.email', 'synthetic@example.invalid')
        (self.repo / 'scripts').mkdir()
        (self.repo / 'docs').mkdir()
        (self.repo / 'AGENTS.md').write_text('synthetic instructions\n')
        (self.repo / 'AI_ENTRYPOINT.md').write_text('synthetic entrypoint\n')
        (self.repo / 'README.md').write_text('# AI Software Factory OSS\n')
        (self.repo / 'scripts' / 'check_all.py').write_text('# synthetic existing validator\n')
        (self.repo / 'docs' / 'HANDOFF_AND_SUCCESSION.md').write_text('# synthetic handoff\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'synthetic baseline')

    def test_preview_is_readonly(self):
        result = installer.install_into_repo(self.repo, ADDON, apply=False)
        self.assertEqual(result['status'], 'DRY_RUN_READY')
        self.assertGreaterEqual(len(result['files']), 10)
        self.assertFalse((self.repo / 'scripts' / 'continuity_watch.py').exists())
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '')

    def test_apply_adds_only_addon_files(self):
        result = installer.install_into_repo(self.repo, ADDON, apply=True)
        self.assertEqual(result['status'], 'INSTALLED_LOCALLY')
        self.assertTrue((self.repo / 'scripts' / 'continuity_watch.py').is_file())
        self.assertTrue((self.repo / 'skills' / 'conversation-archive' / 'SKILL.md').is_file())
        self.assertEqual((self.repo / 'scripts' / 'check_all.py').read_text(), '# synthetic existing validator\n')
        self.assertTrue(git(self.repo, 'status', '--porcelain'))

    def test_collisions_are_rejected_without_partial_copy(self):
        path = self.repo / 'scripts' / 'continuity_watch.py'
        path.write_text('conflicting preexisting file\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'introduce conflict')
        with self.assertRaises(installer.InstallError):
            installer.install_into_repo(self.repo, ADDON, apply=True)
        self.assertEqual(path.read_text(), 'conflicting preexisting file\n')
        self.assertFalse((self.repo / 'scripts' / 'conversation_archive.py').exists())

    def test_main_branch_rejected(self):
        git(self.repo, 'branch', '-m', 'main')
        with self.assertRaises(installer.InstallError):
            installer.install_into_repo(self.repo, ADDON, apply=True)
        self.assertFalse((self.repo / 'scripts' / 'continuity_watch.py').exists())

    def test_dirty_worktree_rejected(self):
        (self.repo / 'AGENTS.md').write_text('modified')
        with self.assertRaises(installer.InstallError):
            installer.install_into_repo(self.repo, ADDON, apply=True)

    def test_other_repository_rejected(self):
        (self.repo / 'README.md').write_text('# Other repo\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'wrong target')
        with self.assertRaises(installer.InstallError):
            installer.install_into_repo(self.repo, ADDON, apply=False)


if __name__ == '__main__':
    unittest.main()
