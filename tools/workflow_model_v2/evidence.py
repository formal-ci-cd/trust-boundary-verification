"""Lossless evidence packing; raw details are retained, not silently discarded."""
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
    # Check original bytes again immediately before pruning loose copies.
    for path in files:
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[str(path.relative_to(directory))]:
            raise ValueError('source changed during packing; originals retained')
    for path in files:
        path.unlink()
    for path in sorted(directory.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    record = dict(format='workflow-v2-evidence-archive-v1', members=manifest,
                  archiveSHA256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  rawFileCount=len(files), retained='all raw details, verified losslessly before pruning')
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
            '個別調査時だけ，別ディレクトリへ展開する．\n')
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


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    record = pack(args.directory)
    print('Packed', record['rawFileCount'], 'detail files into details.tar.gz')
