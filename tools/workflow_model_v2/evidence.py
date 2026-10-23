# 検証結果の追加archiveを作り，内容を照合してから復元できるようにする任意の補助機能．
# archiveを作っても元の出力は削除しない．
# 既存ファイルとの衝突や不正な復元パスは拒否する．
"""検証結果を削除せず，追加archiveの作成・照合・復元を支援する．

入力は通常fileの一覧や検証結果directory，archiveとmanifest，出力は保存物と照合件数である．
archive作成後にmemberのSHA-256を読み直し，復元前には全内容と全出力先を確認する．
異なる既存fileや危険なmemberを見つけると停止し，元のfileを置き換えない．
この補助処理はcompilerの変換やNuSMVの判定規則を変更しない．CLIから任意に使用する．
"""
from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
VISIBLE = {
    'README.md',
    'verification-summary.json',
    'report-metrics.json',
    'unknown-unsupported-inventory.json',
    'legacy-mutation-comparison.json',
    'runtime.json',
    'docker-images.txt'
}

# 新規archiveの全memberをSHA-256で再照合する．
# 元ファイルは保持する．
def archive_verified(files, root, destination):
    """file一覧の追加archiveを作り，全memberの内容を照合する．"""
    root, destination = (Path(root).resolve(), Path(destination))
    if destination.exists():
        raise FileExistsError(destination)
    # archiveの相対member名と元内容のSHA-256を対応付ける表を作る．
    manifest = {}
    # 対象の通常fileを1件ずつ調べ，同じ入力一覧をarchive作成前後の照合に使う．
    for path in files:
        path = Path(path)
        # symlinkや非通常fileはarchiveの入力に受け付けず，実体を推測してたどらない．
        if path.is_symlink() or not path.is_file():
            raise ValueError('archive accepts regular files only: ' + str(path))
        # 保存rootからの相対名をmember名にし，復元先の下へ配置できる形にする．
        name = str(path.relative_to(root))
        if '..' in Path(name).parts:
            raise ValueError('invalid archive path')
        # archive化前のbyte列からhashを計算し，書込み後の再読込みと比較する基準にする．
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 新規作成専用でarchiveを書き，閉じた後に全memberを読み直して照合する．
    with destination.open('xb') as stream:
        # gzipの時刻と名前を固定し，圧縮出力へtarを書き込むstreamを用意する．
        with gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                # 対象の通常fileを1件ずつ調べ，同じ入力一覧をarchive作成前後の照合に使う．
                for path in files:
                    # 指定memberだけを追加し，recursive=Falseでディレクトリ配下を暗黙に収集しない．
                    archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
    with tarfile.open(destination, 'r:gz') as archive:
        # archiveのmember集合と入力一覧が一致することを確認し，不足・余分を検出する．
        if set(archive.getnames()) != set(manifest):
            raise ValueError('archive member mismatch; originals retained')
        for name, expected in manifest.items():
            # archiveから読み戻した内容のhashが入力時と同じかを照合する．
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest() != expected:
                raise ValueError('archive verification failed; originals retained: ' + name)
    return manifest

def pack(directory):
    """検証directoryの詳細fileをarchiveし，保持内容のmanifestを保存する．"""
    directory = Path(directory).resolve()
    if not (directory / 'verification-summary.json').is_file():
        raise ValueError('not a verified evaluation directory')
    # 可視の要約fileを除いた詳細fileを安定した順で集め，追加コピーの対象にする．
    files = sorted((p for p in directory.rglob('*') if p.is_file() and (not (p.parent == directory and p.name in VISIBLE))))
    archive = directory / 'details.tar.gz'
    # 全memberを照合するarchive作成処理を呼び，元fileはそのまま保持する．
    manifest = archive_verified(files, directory, archive)
    # archiveは追加のコピーなので，元のbyte列も再照合し，削除しない．
    # 対象の通常fileを1件ずつ調べ，同じ入力一覧をarchive作成前後の照合に使う．
    for path in files:
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[str(path.relative_to(directory))]:
            raise ValueError('source changed during packing; originals retained')
    # memberごとのhashとarchive全体のhash，元fileを保持した事実を記録する．
    record = dict(
        format='workflow-v2-evidence-archive-v2',
        members=manifest,
        archiveSHA256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        rawFileCount=len(files),
        retained='archive copy plus all original files; no deletion'
    )
    (directory / 'pack-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    if not (directory / 'README.md').exists():
        summary = json.loads((directory / 'verification-summary.json').read_text())
        (directory / 'README.md').write_text(f"# 検証結果\n\n対象 {summary['total']}モデル，有効 {summary['validated']}モデル，構文エラー対照 {summary['syntaxControls']}件．想定外の失敗：{summary['failures']}．\n\nproperty結果は verification-summary.json．unsupportedを安全とは判定しない．\n\n全SMV・入力YAML・trace・source map・ログは details.tar.gz に無損失保存した．pack-manifest.json に各memberとarchiveのSHA-256を記録した．個別ファイルも元の配置にすべて保持している．\n")
    return record

def read_prototype(root, relative):
    """通常fileを優先し，なければ保存archive内の元データを読む．"""
    root = Path(root)
    plain = root / relative
    # 通常配置のfileがあればそれを読み，なければ過去archiveのmemberを読む．
    if plain.is_file():
        return plain.read_text()
    archive_path = root / 'archive/prototype-2026-10-22.tar.gz'
    with tarfile.open(archive_path, 'r:gz') as archive:
        member = archive.getmember(relative)
        if not member.isfile():
            raise ValueError('expected regular evidence file')
        return archive.extractfile(member).read().decode()

# 復元先との衝突とarchive内のpathを確認し，照合後に個別ファイルを復元する．
def restore(archive_path, directory, manifest_path):
    """全内容と復元先を事前照合してから，未存在fileだけを復元する．"""
    archive_path, directory = (Path(archive_path), Path(directory).resolve())
    manifest = json.loads(Path(manifest_path).read_text())
    # 復元前にarchive全体のhashを照合し，異なる保存物を誤って展開しない．
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != manifest['archiveSHA256']:
        raise ValueError('archive SHA-256 mismatch')
    # まず全memberをmemoryへ取り込み，hashとpathを確認する．
    # 検査中は復元先へ書かない．
    # 復元候補のbyte列を全件memoryへ読み，検査が終わるまで保存先には書かない．
    payloads = {}
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive.getmembers():
            # archiveのmember名をpathとして検査し，絶対pathや親参照を拒否する準備をする．
            relative = Path(member.name)
            # 通常file以外・絶対path・..・重複名をまとめて拒否し，不正な出力先を作らない．
            if not member.isfile() or relative.is_absolute() or '..' in relative.parts or (member.name in payloads):
                raise ValueError('unsafe or duplicate archive member: ' + member.name)
            # 圧縮memberの内容を読み，manifestのhashと照合する．
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != manifest['members'].get(member.name):
                raise ValueError('member SHA-256 mismatch: ' + member.name)
            # 照合が済んだ内容だけを保持し，後の出力先検査と新規作成へ渡す．
            payloads[member.name] = data
    if set(payloads) != set(manifest['members']):
        raise ValueError('archive member list mismatch')
    # 途中まで復元してから衝突を見つけないよう，全出力先を先に確認する．
    for name, data in payloads.items():
        path = directory / name
        # 出力fileとその全祖先をたどり，復元root内のsymlink経由の書込みを防ぐ．
        for parent in (path, *path.parents):
            # 指定した復元rootまで調べたら，その外の祖先には進まない．
            if parent == directory:
                break
            if parent.is_symlink():
                raise ValueError('symlink in restoration path: ' + str(parent))
        # 既存fileが異内容または非通常fileなら保存したまま停止し，置き換えない．
        if path.exists() and (not path.is_file() or path.read_bytes() != data):
            raise FileExistsError('existing output differs; preserved: ' + str(path))
    # 全出力先の事前確認が終わってから，新規fileだけを作る．
    # 同一の既存fileは保持する．
    created = 0
    for name, data in payloads.items():
        path = directory / name
        # 同一の既存fileは保持し，存在しないfileだけを作成する．
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # binaryの新規作成専用で開き，事前検査後に存在したfileも上書きしない．
            with path.open('xb') as stream:
                stream.write(data)
            # 実際に新規作成した件数だけを数え，同一内容で保持した既存fileと区別する．
            created += 1
    return dict(verified=len(payloads), restored=created, identicalExisting=len(payloads) - created)
# import時にはCLIを起動せず，このファイルを直接実行した場合だけ入口を呼ぶ．
if __name__ == '__main__':
    import argparse
    # 受け付ける引数を登録する解析器を作り，必須指定・型変換・help表示を管理する．
    parser = argparse.ArgumentParser(description=__doc__)
    # backup/restoreを必須のサブコマンドとして分け，args.commandで選択結果を受け取る．
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup', help='create an extra archive; keep every original')
    backup.add_argument('directory', type=Path)
    recovery = commands.add_parser(
        'restore',
        help='restore verified original files without overwriting conflicts'
    )
    recovery.add_argument('archive', type=Path)
    recovery.add_argument('directory', type=Path)
    recovery.add_argument('--manifest', type=Path, required=True)
    # 指定されたコマンドラインを解析し，各引数をargsの属性へ格納する．
    args = parser.parse_args()
    if args.command == 'backup':
        record = pack(args.directory)
        print('Copied', record['rawFileCount'], 'detail files to details.tar.gz; all originals retained')
    else:
        print(json.dumps(restore(args.archive, args.directory, args.manifest)))
