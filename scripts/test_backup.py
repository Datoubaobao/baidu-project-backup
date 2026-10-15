"""Local-only integration checks; never touches a real account or project."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('backup.py')


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / '中文项目 space'
        self.project.mkdir()
        (self.project / '.env').write_bytes(b'SECRET=example\n')
        (self.project / '.git').mkdir()
        (self.project / '.git/config').write_text('git metadata')
        (self.project / 'empty').mkdir()
        (self.project / 'AGENTS.md').write_text('original project instructions')
        self.payload = bytes(range(256)) * 10000
        (self.project / 'movie.bin').write_bytes(self.payload)
        (self.project / 'run.sh').write_text('#!/bin/sh\ntrue\n')
        (self.project / 'run.sh').chmod(0o755)
        self.external = self.base / 'outside.txt'
        self.external.write_text('external target')
        (self.project / 'outside-link').symlink_to(self.external)
        (self.project / 'inside-link').symlink_to('.env')

    def tearDown(self):
        self.temp.cleanup()

    def run_tool(self, *args, good=True):
        p = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                           capture_output=True, text=True)
        if good:
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode, 0, p.stdout)
        return p

    def prepare(self):
        result = self.run_tool('prepare', '--project', self.project,
                              '--state-root', self.base / 'state', '--chunk-mib', '1')
        self.record_path = Path(result['record'])
        self.record = json.loads(self.record_path.read_text())
        self.stage = Path(self.record['staging_dir'])
        self.download = self.base / 'download'
        shutil.copytree(self.stage, self.download)
        return result

    def verify(self):
        return self.run_tool('verify', '--record', self.record_path,
                             '--download-dir', self.download,
                             '--account-label', 'verified-own-account')

    def cleanup(self, good=True):
        return self.run_tool('cleanup', '--record', self.record_path,
                             '--authorization', 'backup-and-cleanup',
                             '--trash-dir', self.base / 'Trash', good=good)

    def test_roundtrip_preserves_hidden_files_links_modes_and_empty_directories(self):
        self.prepare()
        self.assertGreater(len(self.record['parts']), 1)
        self.verify()
        target = self.base / 'restored'
        self.run_tool('restore', '--record', self.record_path,
                      '--download-dir', self.download, '--destination', target)
        self.assertEqual((target / '.env').read_bytes(), b'SECRET=example\n')
        self.assertEqual((target / 'movie.bin').read_bytes(), self.payload)
        self.assertEqual((target / '.git/config').read_text(), 'git metadata')
        self.assertTrue((target / 'empty').is_dir())
        self.assertEqual(os.readlink(target / 'inside-link'), '.env')
        self.assertEqual(os.readlink(target / 'outside-link'), str(self.external))
        self.assertEqual((target / 'run.sh').stat().st_mode & 0o777, 0o755)
        self.assertEqual((target / 'AGENTS.md').read_text(), 'original project instructions')

    def test_corrupt_or_missing_download_cannot_authorize_cleanup(self):
        self.prepare()
        part = self.download / self.record['parts'][0]['name']
        with part.open('r+b') as f:
            f.write(b'broken')
        self.run_tool('verify', '--record', self.record_path, '--download-dir', self.download,
                      '--account-label', 'own-account', good=False)
        self.cleanup(good=False)
        self.assertTrue((self.project / 'movie.bin').exists())

    def test_local_staging_is_not_a_remote_download(self):
        self.prepare()
        self.run_tool('verify', '--record', self.record_path, '--download-dir', self.stage,
                      '--account-label', 'own-account', good=False)
        self.cleanup(good=False)

    def test_new_or_changed_files_after_backup_block_cleanup(self):
        self.prepare()
        self.verify()
        (self.project / 'new.txt').write_text('new work')
        self.cleanup(good=False)
        self.assertEqual((self.project / 'new.txt').read_text(), 'new work')

    def test_repeated_backup_uses_new_snapshot_and_previous_generated_markers_are_recognized(self):
        self.prepare()
        self.verify()
        old_id = self.record['backup_id']
        (self.project / 'new.txt').write_text('next version')
        r = self.run_tool('prepare', '--project', self.project, '--state-root', self.base / 'state')
        record = json.loads(Path(r['record']).read_text())
        self.assertNotEqual(record['backup_id'], old_id)
        manifest = json.loads((Path(r['staging_dir']) / 'manifest.json').read_text())
        self.assertIn('new.txt', [e['path'] for e in manifest['entries']])
        self.assertNotIn('.codex-baidu-backup.json', [e['path'] for e in manifest['entries']])

    def test_changed_content_with_original_size_and_timestamp_blocks_cleanup(self):
        self.prepare()
        self.verify()
        p = self.project / '.env'
        st = p.stat()
        p.write_bytes(b'SECRET=changed\n')
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.cleanup(good=False)
        self.assertTrue(p.exists())

    @unittest.skipUnless(sys.platform == 'darwin' or hasattr(os, 'setxattr'), 'requires extended attributes')
    def test_restore_preserves_extended_attributes(self):
        p = self.project / 'run.sh'
        name = 'com.codex.backup-test' if sys.platform == 'darwin' else 'user.codex-backup-test'
        if sys.platform == 'darwin':
            subprocess.run(['/usr/bin/xattr', '-w', name, 'attribute-data', str(p)], check=True)
        else:
            os.setxattr(p, name, b'attribute-data')
        self.prepare()
        self.verify()
        target = self.base / 'restored'
        self.run_tool('restore', '--record', self.record_path,
                      '--download-dir', self.download, '--destination', target)
        if sys.platform == 'darwin':
            value = subprocess.run(['/usr/bin/xattr', '-p', name, str(target / 'run.sh')],
                                   capture_output=True, check=True).stdout.strip()
        else:
            value = os.getxattr(target / 'run.sh', name)
        self.assertEqual(value, b'attribute-data')

    def test_cleanup_requires_explicit_instruction_and_leaves_two_markers(self):
        self.prepare()
        self.verify()
        self.run_tool('cleanup', '--record', self.record_path,
                      '--trash-dir', self.base / 'Trash', good=False)
        result = self.cleanup()
        moved = Path(result['trash_path'])
        self.assertEqual((moved / 'movie.bin').read_bytes(), self.payload)
        self.assertTrue((self.project / 'AGENTS.md').exists())
        self.assertTrue((self.project / '.codex-baidu-backup.json').exists())
        self.assertTrue(self.record_path.exists())
        self.assertFalse((self.project / 'movie.bin').exists())
        self.assertEqual(self.external.read_text(), 'external target')

    def test_restore_does_not_overwrite_existing_files_and_supports_one_file(self):
        self.prepare()
        self.verify()
        self.run_tool('restore', '--record', self.record_path, '--download-dir', self.download,
                      '--destination', self.project, good=False)
        target = self.base / 'selected'
        self.run_tool('restore', '--record', self.record_path, '--download-dir', self.download,
                      '--destination', target, '--only', '.git/config')
        self.assertEqual((target / '.git/config').read_text(), 'git metadata')
        self.assertFalse((target / 'movie.bin').exists())

    def test_one_file_restore_downloads_only_required_volumes(self):
        self.prepare()
        self.verify()
        needed = self.run_tool('needed', '--record', self.record_path,
                               '--download-dir', self.download, '--only', '.git/config')
        self.assertLess(len(needed['parts']), len(self.record['parts']))
        keep = {p['name'] for p in needed['parts']}
        for part in self.record['parts']:
            if part['name'] not in keep:
                (self.download / part['name']).unlink()
        target = self.base / 'selected'
        self.run_tool('restore', '--record', self.record_path, '--download-dir', self.download,
                      '--destination', target, '--only', '.git/config')
        self.assertEqual((target / '.git/config').read_text(), 'git metadata')
        self.assertFalse((target / 'movie.bin').exists())

    def test_selective_file_spanning_volumes_keeps_exact_content(self):
        self.prepare()
        self.verify()
        target = self.base / 'selected-large'
        self.run_tool('restore', '--record', self.record_path, '--download-dir', self.download,
                      '--destination', target, '--only', 'movie.bin')
        self.assertEqual((target / 'movie.bin').read_bytes(), self.payload)

    def test_empty_file_and_external_symlink_restore_without_volume_downloads(self):
        (self.project / 'zero.txt').touch()
        self.prepare()
        self.verify()
        for part in self.record['parts']:
            (self.download / part['name']).unlink()
        for item in ['zero.txt', 'outside-link', 'empty']:
            needed = self.run_tool('needed', '--record', self.record_path,
                                   '--download-dir', self.download, '--only', item)
            self.assertEqual(needed['parts'], [])
            target = self.base / ('only-' + item)
            self.run_tool('restore', '--record', self.record_path, '--download-dir', self.download,
                          '--destination', target, '--only', item)
            self.assertTrue((target / item).exists())
        self.assertEqual(self.external.read_text(), 'external target')

    def test_missing_volume_blocks_verification_and_cleanup(self):
        self.prepare()
        (self.download / self.record['parts'][-1]['name']).unlink()
        self.run_tool('verify', '--record', self.record_path, '--download-dir', self.download,
                      '--account-label', 'own-account', good=False)
        self.cleanup(good=False)
        self.assertEqual((self.project / 'movie.bin').read_bytes(), self.payload)

    def test_retry_check_reuses_prepared_archive_without_claiming_remote_success(self):
        self.prepare()
        original_record = self.record_path.read_bytes()
        result = self.run_tool('check', '--record', self.record_path)
        self.assertTrue(result['resume_ready'])
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(self.record_path.read_bytes(), original_record)
        self.assertFalse((self.project / '.codex-baidu-backup.json').exists())

    def test_retry_check_rejects_stale_archive_when_source_changed(self):
        self.prepare()
        (self.project / 'new.txt').write_text('new version')
        self.run_tool('check', '--record', self.record_path, good=False)
        self.assertTrue((self.project / 'movie.bin').exists())

    def test_install_preserves_new_file_created_in_stub(self):
        self.prepare()
        self.verify()
        self.cleanup()
        (self.project / 'new-work.txt').write_text('must preserve')
        restored = self.base / 'restored'
        self.run_tool('restore', '--record', self.record_path,
                      '--download-dir', self.download, '--destination', restored)
        self.run_tool('install', '--record', self.record_path,
                      '--restored-dir', restored, '--trash-dir', self.base / 'Trash', good=False)
        self.assertEqual((self.project / 'new-work.txt').read_text(), 'must preserve')
        self.assertEqual((restored / 'movie.bin').read_bytes(), self.payload)

    def test_full_restore_can_replace_an_empty_original_directory(self):
        self.prepare()
        self.verify()
        self.cleanup()
        shutil.rmtree(self.project)
        self.project.mkdir()
        restored = self.base / 'restored'
        self.run_tool('restore', '--record', self.record_path,
                      '--download-dir', self.download, '--destination', restored)
        self.run_tool('install', '--record', self.record_path,
                      '--restored-dir', restored, '--trash-dir', self.base / 'Trash')
        self.assertEqual((self.project / 'movie.bin').read_bytes(), self.payload)

    def test_edited_json_marker_is_protected_from_backup_verify_and_cleanup(self):
        self.prepare()
        self.verify()
        path = self.project / '.codex-baidu-backup.json'
        marker = json.loads(path.read_text())
        marker['user_note'] = 'must preserve this manual addition'
        marker['remote_path'] = '/user-manual-location'
        path.write_text(json.dumps(marker))
        self.run_tool('prepare', '--project', self.project,
                      '--state-root', self.base / 'state', good=False)
        self.run_tool('verify', '--record', self.record_path, '--download-dir', self.download,
                      '--account-label', 'own-account', good=False)
        self.cleanup(good=False)
        self.assertEqual(json.loads(path.read_text())['user_note'],
                         'must preserve this manual addition')
        self.assertTrue((self.project / 'movie.bin').exists())

    def test_full_restore_can_replace_only_the_generated_project_stub(self):
        self.prepare()
        self.verify()
        self.cleanup()
        restored = self.base / 'restored'
        self.run_tool('restore', '--record', self.record_path,
                      '--download-dir', self.download, '--destination', restored)
        self.run_tool('install', '--record', self.record_path,
                      '--restored-dir', restored, '--trash-dir', self.base / 'Trash')
        self.assertEqual((self.project / 'AGENTS.md').read_text(), 'original project instructions')
        self.assertEqual((self.project / 'movie.bin').read_bytes(), self.payload)
        self.assertFalse((self.project / '.codex-baidu-backup.json').exists())

    def test_external_staging_keeps_index_in_separate_state_root(self):
        result = self.run_tool('prepare', '--project', self.project,
                               '--state-root', self.base / 'state',
                               '--staging-root', self.base / 'external')
        self.assertTrue(Path(result['record']).is_relative_to((self.base / 'state').resolve()))
        self.assertTrue(Path(result['staging_dir']).is_relative_to((self.base / 'external').resolve()))

    def test_install_uses_new_restore_download_after_old_download_was_removed(self):
        self.prepare()
        self.verify()
        self.cleanup()
        new_download = self.base / 'new-download'
        shutil.copytree(self.download, new_download)
        shutil.rmtree(self.download)
        restored = self.base / 'restored'
        self.run_tool('restore', '--record', self.record_path,
                      '--download-dir', new_download, '--destination', restored)
        self.run_tool('install', '--record', self.record_path,
                      '--restored-dir', restored, '--trash-dir', self.base / 'Trash')
        self.assertEqual((self.project / 'movie.bin').read_bytes(), self.payload)

    def test_stage_inside_project_is_rejected(self):
        self.run_tool('prepare', '--project', self.project,
                      '--state-root', self.project / 'state', good=False)

    def test_archive_cannot_escape_destination_even_if_local_record_is_modified(self):
        import io
        import tarfile
        self.prepare()
        evil = self.download / 'project.tar.part00001'
        with tarfile.open(evil, 'w') as tf:
            m = tarfile.TarInfo('../escaped')
            m.size = 3
            tf.addfile(m, io.BytesIO(b'bad'))
        manifest = {'entries': [{'path': '../escaped', 'kind': 'file', 'size': 3,
                                'sha256': hashlib.sha256(b'bad').hexdigest(),
                                'mode': 420, 'mtime_ns': 0, 'xattrs': {}}]}
        raw = json.dumps(manifest).encode()
        (self.download / 'manifest.json').write_bytes(raw)
        self.record['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        self.record['parts'] = [{'name': evil.name, 'size': evil.stat().st_size,
                                'sha256': hashlib.sha256(evil.read_bytes()).hexdigest()}]
        self.record_path.write_text(json.dumps(self.record))
        self.run_tool('restore', '--record', self.record_path, '--download-dir', self.download,
                      '--destination', self.base / 'target', good=False)
        self.assertFalse((self.base / 'escaped').exists())


if __name__ == '__main__':
    unittest.main()
