"""Optional verified archive copies and restoration; never delete raw outputs."""
from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
import tarfile


VISIBLE = {'README.md', 'verification-summary.json', 'report-metrics.json',
           'unknown-unsupported-inventory.json', 'legacy-mutation-comparison.json',
           'runtime.json', 'docker-images.txt'}


def archive_verified(files, root, destination):
    """Write once, verify every member, then return a content manifest."""
    root, destination = Path(root).resolve(), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    manifest = {}
    for path in files:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('archive accepts regular files only: ' + str(path))
        name = str(path.relative_to(root))
        if '..' in Path(name).parts:
            raise ValueError('invalid archive path')
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream:
        with gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for path in files:
                    archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
    with tarfile.open(destination, 'r:gz') as archive:
        if set(archive.getnames()) != set(manifest):
            raise ValueError('archive member mismatch; originals retained')
        for name, expected in manifest.items():
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest() != expected:
                raise ValueError('archive verification failed; originals retained: ' + name)
    return manifest


def pack(directory):
    directory = Path(directory).resolve()
    if not (directory / 'verification-summary.json').is_file():
        raise ValueError('not a verified evaluation directory')
    files = sorted(p for p in directory.rglob('*') if p.is_file() and
                   not (p.parent == directory and p.name in VISIBLE))
    archive = directory / 'details.tar.gz'
    manifest = archive_verified(files, directory, archive)
    # Recheck original bytes; the archive is an additional copy only.
    for path in files:
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[str(path.relative_to(directory))]:
            raise ValueError('source changed during packing; originals retained')
    record = dict(format='workflow-v2-evidence-archive-v2', members=manifest,
                  archiveSHA256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  rawFileCount=len(files), retained='archive copy plus all original files; no deletion')
    (directory / 'pack-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    if not (directory / 'README.md').exists():
        summary = json.loads((directory / 'verification-summary.json').read_text())
        (directory / 'README.md').write_text(
            '# 検証結果\n\n'
            f'対象 {summary["total"]}モデル，有効 {summary["validated"]}モデル，'
            f'構文エラー対照 {summary["syntaxControls"]}件．想定外の失敗：{summary["failures"]}．\n\n'
            'property結果は verification-summary.json．unsupportedを安全とは判定しない．\n\n'
            '全SMV・入力YAML・trace・source map・ログは details.tar.gz に無損失保存した．'
            'pack-manifest.json に各memberとarchiveのSHA-256を記録した．'
            '個別ファイルも元の配置にすべて保持している．\n')
    return record


def read_prototype(root, relative):
    root = Path(root)
    plain = root / relative
    if plain.is_file():
        return plain.read_text()
    archive_path = root / 'archive/prototype-2026-10-22.tar.gz'
    with tarfile.open(archive_path, 'r:gz') as archive:
        member = archive.getmember(relative)
        if not member.isfile():
            raise ValueError('expected regular evidence file')
        return archive.extractfile(member).read().decode()



def restore(archive_path, directory, manifest_path):
    """Verify the complete archive, then restore files without replacing conflicts."""
    archive_path, directory = Path(archive_path), Path(directory).resolve()
    manifest = json.loads(Path(manifest_path).read_text())
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != manifest['archiveSHA256']:
        raise ValueError('archive SHA-256 mismatch')
    payloads = {}
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive.getmembers():
            relative = Path(member.name)
            if (not member.isfile() or relative.is_absolute() or '..' in relative.parts
                    or member.name in payloads):
                raise ValueError('unsafe or duplicate archive member: ' + member.name)
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != manifest['members'].get(member.name):
                raise ValueError('member SHA-256 mismatch: ' + member.name)
            payloads[member.name] = data
    if set(payloads) != set(manifest['members']):
        raise ValueError('archive member list mismatch')
    # Preflight every destination before writing any file.
    for name, data in payloads.items():
        path = directory / name
        for parent in (path, *path.parents):
            if parent == directory:
                break
            if parent.is_symlink():
                raise ValueError('symlink in restoration path: ' + str(parent))
        if path.exists() and (not path.is_file() or path.read_bytes() != data):
            raise FileExistsError('existing output differs; preserved: ' + str(path))
    created = 0
    for name, data in payloads.items():
        path = directory / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(data)
            created += 1
    return dict(verified=len(payloads), restored=created, identicalExisting=len(payloads) - created)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup', help='create an extra archive; keep every original')
    backup.add_argument('directory', type=Path)
    recovery = commands.add_parser('restore', help='restore verified original files without overwriting conflicts')
    recovery.add_argument('archive', type=Path)
    recovery.add_argument('directory', type=Path)
    recovery.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'backup':
        record = pack(args.directory)
        print('Copied', record['rawFileCount'], 'detail files to details.tar.gz; all originals retained')
    else:
        print(json.dumps(restore(args.archive, args.directory, args.manifest)))
