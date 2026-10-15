"""Local archive/verification/recovery mechanics. Cloud transfers happen separately."""
import argparse
import base64
import ctypes
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
import uuid

MARKER = '.codex-baidu-backup.json'
NOTE = '百度网盘恢复说明.md'
BUFFER = 1024 * 1024

if sys.platform == 'darwin':
    LIBC = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    LIBC.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    LIBC.listxattr.restype = ctypes.c_ssize_t
    LIBC.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                            ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    LIBC.getxattr.restype = ctypes.c_ssize_t
    LIBC.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                            ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    LIBC.setxattr.restype = ctypes.c_int


def mac_result(result, path):
    if result < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()), str(path))
    return result


def fail(message):
    raise ValueError(message)


def digest_file(path):
    with open(path, 'rb') as f:
        return digest_stream(f)


def digest_stream(f):
    h = hashlib.sha256()
    while True:
        data = f.read(BUFFER)
        if not data:
            return h.hexdigest()
        h.update(data)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('xb') as f:
        os.chmod(tmp, 0o600)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return hashlib.sha256(raw).hexdigest()


def safe_relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '.' == name or '\x00' in name:
        fail('Unsafe archive path: ' + repr(name))
    if str(p) != name:
        fail('Non-canonical archive path: ' + repr(name))
    return p


def protect_project(path):
    raw = Path(path).expanduser().absolute()
    if raw.is_symlink():
        fail('Project root must not be a symlink')
    p = raw.resolve(strict=True)
    home = Path.home().resolve()
    protected = {Path('/'), Path('/Users'), home, home / 'Desktop',
                 home / 'Documents', home / 'Downloads', home / '.codex',
                 home / '.codex/skills', home / '.Trash'}
    if p in protected or not p.is_dir() or p.is_relative_to(home / '.Trash'):
        fail('Select an individual project folder, not a system/personal root')
    return p


def attrs(path):
    if sys.platform == 'darwin':
        p = os.fsencode(path)
        count = mac_result(LIBC.listxattr(p, None, 0, 1), path)
        names = ctypes.create_string_buffer(count)
        count = mac_result(LIBC.listxattr(p, names, count, 1), path)
        result = {}
        for name in names.raw[:count].split(b'\0'):
            if not name:
                continue
            size = mac_result(LIBC.getxattr(p, name, None, 0, 0, 1), path)
            value = ctypes.create_string_buffer(size)
            size = mac_result(LIBC.getxattr(p, name, value, size, 0, 1), path)
            result[os.fsdecode(name)] = base64.b64encode(value.raw[:size]).decode()
        return dict(sorted(result.items()))
    if not hasattr(os, 'listxattr'):
        return {}
    return {n: base64.b64encode(os.getxattr(path, n, follow_symlinks=False)).decode()
            for n in sorted(os.listxattr(path, follow_symlinks=False))}


def set_attr(path, name, value):
    if sys.platform == 'darwin':
        mac_result(LIBC.setxattr(os.fsencode(path), os.fsencode(name), value,
                                len(value), 0, 1), path)
    else:
        os.setxattr(path, name, value, follow_symlinks=False)


def inventory(project):
    entries = []

    def visit(directory):
        for item in sorted(os.scandir(directory), key=lambda i: i.name):
            p = Path(item.path)
            rel = p.relative_to(project).as_posix()
            safe_relative(rel)
            st = p.lstat()
            e = {'path': rel, 'mode': stat.S_IMODE(st.st_mode),
                 'mtime_ns': st.st_mtime_ns, 'xattrs': attrs(p)}
            if stat.S_ISLNK(st.st_mode):
                e.update(kind='symlink', target=os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                e['kind'] = 'directory'
            elif stat.S_ISREG(st.st_mode):
                e.update(kind='file', size=st.st_size, sha256=digest_file(p))
                after = p.lstat()
                if (after.st_ino, after.st_size, after.st_mtime_ns) != (st.st_ino, st.st_size, st.st_mtime_ns):
                    fail('File changed while scanning: ' + rel)
            else:
                fail('Unsupported special file (socket/device/FIFO): ' + rel)
            entries.append(e)
            if e['kind'] == 'directory':
                visit(p)

    visit(project)
    return entries


class ChunkWriter:
    def __init__(self, folder, limit):
        self.folder, self.limit = folder, limit
        self.file = None
        self.parts = []

    def write(self, data):
        remaining = memoryview(data)
        while remaining:
            if self.file is None:
                name = 'project.tar.part' + str(len(self.parts) + 1).zfill(5)
                self.file = (self.folder / name).open('xb')
                self.current = {'name': name, 'size': 0}
                self.hash = hashlib.sha256()
            n = min(len(remaining), self.limit - self.current['size'])
            self.file.write(remaining[:n])
            self.hash.update(remaining[:n])
            self.current['size'] += n
            remaining = remaining[n:]
            if self.current['size'] == self.limit:
                self.close_part()
        return len(data)

    def close_part(self):
        if self.file is not None:
            self.file.flush()
            os.fsync(self.file.fileno())
            self.file.close()
            self.current['sha256'] = self.hash.hexdigest()
            self.parts.append(self.current)
            self.file = None


class PartsReader:
    def __init__(self, folder, parts):
        self.paths = iter(folder / p['name'] for p in parts)
        self.current = None

    def read(self, size):
        chunks, left = [], size
        while left:
            if self.current is None:
                p = next(self.paths, None)
                if p is None:
                    break
                self.current = p.open('rb')
            chunk = self.current.read(left)
            if chunk:
                chunks.append(chunk)
                left -= len(chunk)
            else:
                self.current.close()
                self.current = None
        return b''.join(chunks)

    def close(self):
        if self.current:
            self.current.close()


def manifest_map(manifest):
    entries = {}
    for entry in manifest['entries']:
        name = entry['path']
        safe_relative(name)
        if name in entries or entry['kind'] not in ('file', 'directory', 'symlink'):
            fail('Duplicate or unsupported manifest entry')
        entries[name] = entry
    for name in entries:
        for parent in PurePosixPath(name).parents:
            if str(parent) == '.':
                continue
            if str(parent) not in entries or entries[str(parent)]['kind'] != 'directory':
                fail('Archive entry has a missing or symlink parent: ' + name)
    return entries


def selected_paths(expected, only):
    if only:
        safe_relative(only)
        if only not in expected:
            fail('Requested path not found in this snapshot: ' + only)
    selected = {n for n in expected if not only or n == only or n.startswith(only + '/')}
    for n in list(selected):
        selected.update(str(p) for p in PurePosixPath(n).parents if str(p) != '.')
    return selected


def content_metadata(e):
    return {k: v for k, v in e.items() if k != 'archive_data_offset'}


def apply_metadata(destination, expected, selected):
    for n in sorted(selected):
        e, p = expected[n], destination / n
        if e['kind'] == 'symlink':
            p.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(e['target'], p)
    for n in sorted(selected, key=lambda n: len(PurePosixPath(n).parts), reverse=True):
        e, p = expected[n], destination / n
        if e['kind'] != 'symlink':
            os.chmod(p, e['mode'])
        os.utime(p, ns=(e['mtime_ns'], e['mtime_ns']), follow_symlinks=False)
        for key, value in e.get('xattrs', {}).items():
            set_attr(p, key, base64.b64decode(value))


def required_parts(record, manifest, only):
    if not only:
        return record['parts']
    expected = manifest_map(manifest)
    selected = selected_paths(expected, only)
    total = sum(p['size'] for p in record['parts'])
    spans = []
    for n in selected:
        e = expected[n]
        if e['kind'] == 'file':
            offset = e.get('archive_data_offset')
            if not isinstance(offset, int) or offset < 0 or offset + e['size'] > total:
                fail('No valid file offset for selective restoration: ' + n)
            if e['size']:
                spans.append((offset, offset + e['size']))
    cursor, result = 0, []
    for p in record['parts']:
        if any(start < cursor + p['size'] and end > cursor for start, end in spans):
            result.append(p)
        cursor += p['size']
    return result


def restore_subset(folder, record, manifest, destination, only):
    expected = manifest_map(manifest)
    selected = selected_paths(expected, only)
    for n in sorted(selected):
        if expected[n]['kind'] == 'directory':
            (destination / n).mkdir(parents=True, exist_ok=True)
    for n in sorted(selected):
        e = expected[n]
        if e['kind'] != 'file':
            continue
        target = destination / n
        target.parent.mkdir(parents=True, exist_ok=True)
        start, end = e['archive_data_offset'], e['archive_data_offset'] + e['size']
        h, count, cursor = hashlib.sha256(), 0, 0
        with target.open('xb') as output:
            for part in record['parts']:
                lo, hi = max(start, cursor), min(end, cursor + part['size'])
                if lo < hi:
                    with (folder / part['name']).open('rb') as src:
                        src.seek(lo - cursor)
                        left = hi - lo
                        while left:
                            data = src.read(min(BUFFER, left))
                            if not data:
                                fail('Incomplete volume while extracting: ' + n)
                            h.update(data)
                            output.write(data)
                            left -= len(data)
                            count += len(data)
                cursor += part['size']
        if count != e['size'] or h.hexdigest() != e['sha256']:
            fail('Selective restore content mismatch: ' + n)
    apply_metadata(destination, expected, selected)
    return selected


def process_archive(folder, parts, manifest, destination=None, only=None):
    expected = manifest_map(manifest)
    selected = selected_paths(expected, only)
    seen = set()
    reader = PartsReader(folder, parts)
    try:
        with tarfile.open(fileobj=reader, mode='r|') as tf:
            for m in tf:
                safe_relative(m.name)
                e = expected.get(m.name)
                if e is None or m.name in seen:
                    fail('Unexpected/duplicate archive entry: ' + m.name)
                seen.add(m.name)
                kind = 'file' if m.isfile() else 'directory' if m.isdir() else 'symlink' if m.issym() else 'unsupported'
                if kind != e['kind'] or stat.S_IMODE(m.mode) != e['mode']:
                    fail('Archive type/mode mismatch: ' + m.name)
                if kind == 'symlink' and m.linkname != e['target']:
                    fail('Symlink target mismatch: ' + m.name)
                target = destination / m.name if destination else None
                if kind == 'file':
                    if m.size != e['size']:
                        fail('Archive size mismatch: ' + m.name)
                    output = None
                    try:
                        if destination and m.name in selected:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            output = target.open('xb')
                        h = hashlib.sha256()
                        src = tf.extractfile(m)
                        while True:
                            data = src.read(BUFFER)
                            if not data:
                                break
                            h.update(data)
                            if output:
                                output.write(data)
                        if h.hexdigest() != e['sha256']:
                            fail('Archive content mismatch: ' + m.name)
                    finally:
                        if output:
                            output.close()
                elif destination and m.name in selected and kind == 'directory':
                    target.mkdir(parents=True, exist_ok=True)
        if seen != set(expected):
            fail('Archive is incomplete')
    finally:
        reader.close()
    if destination:
        # Create symlinks only after all regular files: no write can traverse one.
        apply_metadata(destination, expected, selected)
    return selected


def load_record(path):
    path = Path(path).expanduser().resolve(strict=True)
    r = json.loads(path.read_text())
    if r.get('schema_version') != 1 or r.get('record_path') != str(path):
        fail('Unsupported/moved backup record')
    return path, r


def marker_exclusions(project):
    """Skip only intact tool-generated locator files from an earlier backup."""
    marker, note = project / MARKER, project / NOTE
    skip = set()
    if marker.exists() or note.exists():
        if marker.is_symlink() or note.is_symlink() or not marker.is_file() or not note.is_file():
            fail('Unrecognized/conflicting recovery marker files')
        old = json.loads(marker.read_text())
        _, indexed = load_record(old['record_path'])
        if old.get('backup_id') != indexed.get('backup_id') or indexed['project_path'] != str(project):
            fail('Recovery marker/index mismatch')
        if digest_file(marker) != indexed.get('marker_sha256'):
            fail('Recovery JSON marker was edited; preserve its contents')
        if indexed['status'] in ('local_trashed', 'cleanup_started', 'cleanup_incomplete'):
            fail('This is an archived project stub; restore it before making a new backup')
        if indexed['status'] != 'remote_verified' or digest_file(note) != indexed.get('marker_note_sha256'):
            fail('Recovery marker changed or is unverified; retain all files')
        skip = {MARKER, NOTE}
    return skip


def snapshot_inventory(project):
    skip = marker_exclusions(project)
    return [e for e in inventory(project) if e['path'] not in skip]


def downloaded_manifest(record, folder):
    folder = Path(folder).expanduser().resolve(strict=True)
    mf = folder / 'manifest.json'
    if mf.is_symlink() or digest_file(mf) != record['manifest_sha256']:
        fail('Downloaded manifest is missing, unsafe, or corrupt')
    manifest = json.loads(mf.read_text())
    manifest_map(manifest)
    return folder, manifest


def validate_download(record, folder, allow_staging=False, only=None):
    folder = Path(folder).expanduser().resolve(strict=True)
    stage = Path(record['staging_dir'])
    project = Path(record['project_path'])
    if not allow_staging and (folder == stage or folder.is_relative_to(stage) or folder.is_relative_to(project)):
        fail('Verification requires a separate download folder outside project and staging')
    folder, manifest = downloaded_manifest(record, folder)
    for p in required_parts(record, manifest, only):
        safe_relative(p['name'])
        if '/' in p['name']:
            fail('Invalid volume filename')
        downloaded = folder / p['name']
        staged = stage / p['name']
        if downloaded.is_symlink() or not downloaded.is_file():
            fail('Downloaded volume missing/unsafe: ' + p['name'])
        if not allow_staging and staged.exists() and downloaded.samefile(staged):
            fail('A local staging file is not a downloaded copy')
        if downloaded.stat().st_size != p['size'] or digest_file(downloaded) != p['sha256']:
            fail('Downloaded volume corrupt: ' + p['name'])
    return folder, manifest


def prepare(args):
    project = protect_project(args.project)
    state = Path(args.state_root).expanduser().resolve()
    if state == project or state.is_relative_to(project):
        fail('Backup staging/index must be outside the project')
    if (project / '.git').is_file():
        fail('Linked Git worktree detected; archive through Codex worktree tools or include external Git storage first')
    if not args.remote_root.startswith('/') or '..' in PurePosixPath(args.remote_root).parts:
        fail('Remote root must be an absolute Baidu Netdisk folder')
    entries = snapshot_inventory(project)
    size = sum(e.get('size', 0) for e in entries)
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    staging_root = Path(args.staging_root).expanduser().resolve() if args.staging_root else state / 'staging'
    if staging_root == project or staging_root.is_relative_to(project):
        fail('Staging folder must be outside the project')
    staging_root.mkdir(parents=True, exist_ok=True)
    # PAX/xattrs/directory headers add overhead even for many tiny files.
    required = size + sum(len(v) for e in entries for v in e['xattrs'].values()) + len(entries) * 8192 + 64 * BUFFER
    if shutil.disk_usage(staging_root).free < required:
        fail('Insufficient staging disk space; select an external --staging-root')
    backup_id = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z') + '-' + uuid.uuid4().hex[:8]
    key = hashlib.sha256(str(project).encode()).hexdigest()[:12]
    stage = staging_root / backup_id
    stage.mkdir(parents=True, mode=0o700)
    record_path = state / 'records' / (backup_id + '.json')
    record = {'schema_version': 1, 'backup_id': backup_id, 'status': 'preparing',
              'created_at': datetime.now().astimezone().isoformat(), 'project_path': str(project),
              'record_path': str(record_path), 'staging_dir': str(stage),
              'remote_path': args.remote_root.rstrip('/') + '/' + project.name + '-' + key + '/' + backup_id,
              'entry_count': len(entries), 'file_bytes': size,
              'external_symlinks': [e['path'] for e in entries if e['kind'] == 'symlink'
                  and not (project / e['path']).resolve(strict=False).is_relative_to(project)],
              'parts': [], 'cleanup_policy': 'explicit-backup-and-cleanup-only'}
    atomic_json(record_path, record)
    manifest = {'schema_version': 1, 'backup_id': backup_id, 'entries': entries}
    record['manifest_sha256'] = atomic_json(stage / 'manifest.json', manifest)
    writer = ChunkWriter(stage, args.chunk_mib * BUFFER)
    try:
        with tarfile.open(fileobj=writer, mode='w|', format=tarfile.PAX_FORMAT) as tf:
            for e in entries:
                m = tarfile.TarInfo(e['path'])
                m.mode, m.mtime = e['mode'], e['mtime_ns'] / 1_000_000_000
                m.pax_headers = {'mtime': str(e['mtime_ns'] / 1_000_000_000)}
                if e['kind'] == 'directory':
                    m.type = tarfile.DIRTYPE
                    tf.addfile(m)
                elif e['kind'] == 'symlink':
                    m.type, m.linkname = tarfile.SYMTYPE, e['target']
                    tf.addfile(m)
                else:
                    m.size = e['size']
                    with (project / e['path']).open('rb') as f:
                        tf.addfile(m, f)
                    e['archive_data_offset'] = tf.offset - ((e['size'] + 511) // 512) * 512
        writer.close_part()
        record['parts'] = writer.parts
        record['manifest_sha256'] = atomic_json(stage / 'manifest.json', manifest)
        process_archive(stage, writer.parts, manifest)
        if snapshot_inventory(project) != [content_metadata(e) for e in entries]:
            fail('Project changed during preparation; stop writers and make a new backup')
        record['status'] = 'prepared'
    except Exception:
        writer.close_part()
        record['parts'] = writer.parts
        record['status'] = 'preparation_failed'
        atomic_json(record_path, record)
        raise
    atomic_json(record_path, record)
    (stage / 'backup-info.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return {'record': str(record_path), 'staging_dir': str(stage),
            'remote_path': record['remote_path'], 'file_bytes': size,
            'required_download_space': sum(p['size'] for p in record['parts']),
            'external_symlinks': record['external_symlinks'], 'status': 'prepared'}


def check(args):
    record_path, r = load_record(args.record)
    if r['status'] not in ('prepared', 'remote_verified'):
        fail('Only a successfully prepared archive can be resumed')
    project = protect_project(r['project_path'])
    _, manifest = validate_download(r, r['staging_dir'], allow_staging=True)
    if snapshot_inventory(project) != [content_metadata(e) for e in manifest['entries']]:
        fail('Project changed since preparation; create a new snapshot instead of resuming this one')
    return {'status': r['status'], 'resume_ready': True, 'record': str(record_path),
            'staging_dir': r['staging_dir'], 'remote_path': r['remote_path'],
            'entry_count': r['entry_count'], 'file_bytes': r['file_bytes'],
            'parts': len(r['parts'])}


def recovery_note(record):
    return ('# 百度网盘项目恢复标记\n\n'
            f"项目原路径：{record['project_path']}\n\n"
            f"百度网盘目录：{record['remote_path']}\n\n"
            f"备份编号：{record['backup_id']}\n\n"
            f"备份时间：{record['created_at']}\n\n"
            f"校验时间：{record.get('verified_at', '尚未完成')}\n\n"
            f"独立索引：{record['record_path']}\n\n"
            '需要文件时，请对 Codex 说：“使用 $baidu-project-backup 恢复这个项目（或指定文件）”。'
            '用百度网盘客户端下载该目录的 manifest.json、backup-info.json 和全部 project.tar.part 分卷，'
            '核对哈希后恢复到新目录。不要把缺少的项目内容当成空项目重新生成。\n\n'
            '备份范围为磁盘上该项目目录的文件，包括隐藏文件。目录外的符号链接目标需要另行备份。'
            'Codex 服务端聊天记录不属于该归档。\n\n'
            '移入废纸篓可撤销；清空废纸篓后才释放空间，本技能不自动清空。\n')


def write_markers(record):
    project = Path(record['project_path'])
    (project / NOTE).write_text(recovery_note(record))
    record['marker_note_sha256'] = digest_file(project / NOTE)
    marker_value = {k: v for k, v in record.items() if k != 'marker_sha256'}
    record['marker_sha256'] = atomic_json(project / MARKER, marker_value)


def verify(args):
    record_path, r = load_record(args.record)
    if r['status'] not in ('prepared', 'remote_verified'):
        fail('Snapshot must be successfully prepared before verification')
    folder, manifest = validate_download(r, args.download_dir)
    process_archive(folder, r['parts'], manifest)
    marker_exclusions(Path(r['project_path']))
    r.update(status='remote_verified', verified_at=datetime.now().astimezone().isoformat(),
             account_label=args.account_label, verified_download_dir=str(folder))
    write_markers(r)
    atomic_json(record_path, r)
    return {'status': r['status'], 'record': str(record_path), 'remote_path': r['remote_path']}


def cleanup(args):
    if args.authorization != 'backup-and-cleanup':
        fail('Cleanup requires explicit user instruction: 备份并清理')
    record_path, r = load_record(args.record)
    if r['status'] != 'remote_verified':
        fail('No verified remote download; local project is retained')
    project = protect_project(r['project_path'])
    folder, manifest = validate_download(r, r['verified_download_dir'])
    # The verification record remains tied to unmodified downloaded volumes.
    if json.loads((project / MARKER).read_text()).get('backup_id') != r['backup_id']:
        fail('Recovery marker belongs to another backup')
    if digest_file(project / NOTE) != r['marker_note_sha256']:
        fail('Recovery note changed; retain local files')
    current = snapshot_inventory(project)
    if current != [content_metadata(e) for e in manifest['entries']]:
        fail('Project changed since backup; retain files and make a new backup')
    trash = Path(args.trash_dir).expanduser().resolve()
    if trash == project or trash.is_relative_to(project):
        fail('Trash/recovery folder must be outside project')
    trash.mkdir(parents=True, exist_ok=True)
    if trash.stat().st_dev != project.stat().st_dev:
        fail('Trash is on a different volume; use the project volume recovery folder')
    target = trash / (project.name + '-baidu-' + r['backup_id'])
    if target.exists():
        fail('Trash target already exists; do not overwrite')
    # Persist a recovery breadcrumb before the atomic same-volume move.
    r.update(status='cleanup_started', trash_path=str(target))
    atomic_json(record_path, r)
    try:
        os.rename(project, target)
        project.mkdir(mode=0o700)
        r.update(status='local_trashed', cleaned_at=datetime.now().astimezone().isoformat())
        write_markers(r)
        (project / 'AGENTS.md').write_text(recovery_note(r))
    except Exception:
        r['status'] = 'cleanup_incomplete'
        atomic_json(record_path, r)
        raise
    atomic_json(record_path, r)
    return {'status': r['status'], 'trash_path': str(target), 'marker_directory': str(project),
            'space_reclaimed_bytes': 0}


def restore(args):
    record_path, r = load_record(args.record)
    folder, manifest = validate_download(r, args.download_dir, only=args.only)
    destination = Path(args.destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        fail('Restore destination must be new; do not overwrite existing files')
    parent = destination.parent.resolve(strict=True)
    if parent.is_relative_to(Path(r['project_path'])):
        fail('Restore outside the original project first')
    destination = parent / destination.name
    selected_bytes = sum(e.get('size', 0) for e in manifest['entries'] if not args.only
                         or e['path'] == args.only or e['path'].startswith(args.only + '/'))
    if shutil.disk_usage(parent).free < selected_bytes + 64 * BUFFER:
        fail('Insufficient restore disk space')
    work = parent / ('.baidu-restore-' + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    try:
        if args.only:
            selected = restore_subset(folder, r, manifest, work, args.only)
        else:
            selected = process_archive(folder, r['parts'], manifest, work)
        actual = inventory(work)
        expected = [content_metadata(e) for e in manifest['entries'] if e['path'] in selected]
        # Creating ancestors for partial restore may change their timestamps;
        # process_archive resets metadata after all writes, so exact equality applies.
        if actual != expected:
            fail('Restored files/metadata do not match snapshot')
        os.rename(work, destination)
    except Exception:
        # Keep partial artifacts for diagnosis, never present them as restored.
        raise
    r.update(last_restore_at=datetime.now().astimezone().isoformat(),
             last_restore_destination=str(destination), last_restore_only=args.only,
             last_restore_download_dir=str(folder))
    atomic_json(record_path, r)
    return {'status': 'restored', 'destination': str(destination), 'only': args.only,
            'record': str(record_path)}


def needed(args):
    _, r = load_record(args.record)
    folder, manifest = downloaded_manifest(r, args.download_dir)
    parts = required_parts(r, manifest, args.only)
    return {'remote_path': r['remote_path'], 'parts': parts,
            'download_bytes': sum(p['size'] for p in parts), 'only': args.only}


def install(args):
    record_path, r = load_record(args.record)
    restored = Path(args.restored_dir).expanduser().resolve(strict=True)
    if str(restored) != r.get('last_restore_destination') or r.get('last_restore_only'):
        fail('Only the successfully verified full restore can replace the project stub')
    project = Path(r['project_path'])
    if restored == project or restored.is_relative_to(project):
        fail('Restored directory must be outside original project')
    folder, manifest = downloaded_manifest(r, r['last_restore_download_dir'])
    if inventory(restored) != [content_metadata(e) for e in manifest['entries']]:
        fail('Restored project changed before installation')
    trash = Path(args.trash_dir).expanduser().resolve()
    if trash == project or trash.is_relative_to(project) or trash.is_relative_to(restored):
        fail('Trash must be outside both project directories')
    if project.exists():
        protect_project(project)
        names = set(p.name for p in project.iterdir())
        if project.is_symlink() or (names and names != {MARKER, NOTE, 'AGENTS.md'}):
            fail('Original folder contains new files; retain them and use a separate restore directory')
        if names:
            if (project / MARKER).is_symlink() or digest_file(project / MARKER) != r.get('marker_sha256'):
                fail('Recovery JSON marker was edited; preserve its contents')
            marker = json.loads((project / MARKER).read_text())
            if marker.get('backup_id') != r['backup_id'] or marker.get('status') != 'local_trashed':
                fail('Original folder is not the expected archived project stub')
            for name in (NOTE, 'AGENTS.md'):
                if (project / name).is_symlink() or digest_file(project / name) != r['marker_note_sha256']:
                    fail('Project stub changed; retain it')
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / ('baidu-stub-' + r['backup_id'] + '-' + uuid.uuid4().hex[:8])
        if project.stat().st_dev != restored.stat().st_dev or trash.stat().st_dev != project.stat().st_dev:
            fail('Install on the same filesystem; otherwise keep the separate restored directory')
        os.rename(project, target)
        try:
            os.rename(restored, project)
        except Exception:
            os.rename(target, project)
            raise
    else:
        if project.parent.stat().st_dev != restored.stat().st_dev:
            fail('Install on the same filesystem; otherwise keep the separate restored directory')
        os.rename(restored, project)
    r.update(last_installed_at=datetime.now().astimezone().isoformat())
    atomic_json(record_path, r)
    return {'status': 'installed', 'project_path': str(project)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--project', required=True)
    p.add_argument('--state-root', default=str(Path.home() / '.codex/project-backups'))
    p.add_argument('--staging-root')
    p.add_argument('--remote-root', default='/Codex项目备份')
    p.add_argument('--chunk-mib', type=int, default=2048)
    for name in ('check', 'verify', 'cleanup', 'restore', 'needed', 'install'):
        q = sub.add_parser(name)
        q.add_argument('--record', required=True)
        if name in ('verify', 'restore', 'needed'):
            q.add_argument('--download-dir', required=True)
        if name == 'verify':
            q.add_argument('--account-label', required=True)
        if name in ('cleanup', 'install'):
            q.add_argument('--trash-dir', default=str(Path.home() / '.Trash'))
        if name == 'cleanup':
            q.add_argument('--authorization')
        if name == 'restore':
            q.add_argument('--destination', required=True)
            q.add_argument('--only')
        if name == 'needed':
            q.add_argument('--only', required=True)
        if name == 'install':
            q.add_argument('--restored-dir', required=True)
    args = parser.parse_args()
    if args.command == 'prepare' and args.chunk_mib < 1:
        fail('chunk-mib must be positive')
    result = globals()[args.command](args)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError) as e:
        print('Backup stopped: ' + str(e), file=sys.stderr)
        sys.exit(1)
