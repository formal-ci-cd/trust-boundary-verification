# NuSMVのコマンド・出力・エラー・時間・終了状態を，1つのログへ保存する．
"""SMVを検査し，実行記録をmodel.smv.logの1ファイルへまとめる．

CTL検査と到達状態列挙を別phaseで実行し，NuSMVの生出力を読みやすい区切り付きで残す．
末尾のJSONは出力のbyte位置と実行状態を持ち，集計側が本文を重複保存せず読み出せる．
既存ログは上書きしない．旧形式の個別ログもread_verificationから読み取れる．
workflowのShellやGitHub操作は実行しない．
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time

# この区切り以降に実行状態と生出力の位置を記録する．最後の区切りだけを使うため，本文中の同じ文字列とは混同しない．
_METADATA_MARKER = b'\n=== TBV-VERIFICATION-METADATA-V1 ===\n'
_LEGACY_SUFFIXES = (
    '.stdout', '.stderr', '.exitcode', '.seconds', '.timing', '.commands',
    '.stats.stdout', '.stats.stderr', '.stats.exitcode',
)


def _now():
    """ログの開始・終了時刻を，timezone付きUTCで返す．"""
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _write(stream, text):
    """文字列をUTF-8へ変換して書き，出力位置の単位をbyteにそろえる．"""
    stream.write(text.encode('utf-8'))


def _run_phase(stream, name, command, timeout, input_commands=None):
    """1回のNuSMV実行を記録し，生出力の位置と終了状態を返す．"""
    started_at = _now()
    _write(stream, f'\n=== {name} ===\n開始時刻: {started_at}\n実行コマンド: {shlex.join(command)}\n')
    if input_commands is not None:
        # 一時command fileは残さず，その内容をログに埋め込んで再確認できるようにする．
        _write(stream, '\n[NuSMVへの入力コマンド]\n' + input_commands)
    _write(stream, '\n[標準出力]\n')
    stdout_start = stream.tell()
    started = time.monotonic()
    timed_out = False
    # stderrの作業領域はコンテナの一時領域だけに作り，終了・失敗時とも最後にログへ回収する．
    with tempfile.TemporaryFile() as errors:
        try:
            # stdoutは直接ログへ流すため，大きな反例や時間切れ直前の出力も保持できる．
            result = subprocess.run(command, stdout=stream, stderr=errors, timeout=timeout)
            process_code = result.returncode
        except subprocess.TimeoutExpired:
            process_code = 124
            timed_out = True
            errors.write(f'\nTimeout after {timeout} seconds; no reachability verdict.\n'.encode())
        except OSError as exc:
            # NuSMVの起動失敗も同じログへ残し，成功したphaseとして扱わない．
            process_code = 127
            errors.write(f'\nCannot start NuSMV: {exc}\n'.encode())
        seconds = time.monotonic() - started
        stdout_end = stream.tell()
        _write(stream, '\n[標準エラー・NuSMV診断]\n')
        stderr_start = stream.tell()
        errors.seek(0)
        while block := errors.read(65536):
            stream.write(block)
        stderr_end = stream.tell()
    finished_at = _now()
    _write(stream, f'\n終了時刻: {finished_at}\nプロセス終了コード: {process_code}\n実行時間（秒）: {seconds:.6f}\n')
    return dict(
        name=name, command=command, inputCommands=input_commands,
        startedAt=started_at, finishedAt=finished_at, seconds=seconds,
        processExitcode=process_code, exitcode=process_code, timedOut=timed_out,
        stdout=dict(offset=stdout_start, length=stdout_end - stdout_start),
        stderr=dict(offset=stderr_start, length=stderr_end - stderr_start),
    )


def _read_text(path):
    """旧形式の任意ログを読み，未存在なら空文字を返す．"""
    return path.read_text() if path.exists() else ''


def read_verification(model):
    """新しい統合ログ，または旧sidecarから，集計用の共通辞書を返す．"""
    model = Path(model)
    log = Path(str(model) + '.log')
    if log.exists():
        raw = log.read_bytes()
        # 末尾まで書かれていないログは，古い結果にfallbackせず未完了として扱う．
        body, marker, footer = raw.rpartition(_METADATA_MARKER)
        if not marker:
            return dict(format='incomplete', modelCheck=dict(
                stdout='', stderr='Verification log is incomplete; no reachability verdict.',
                exitcode=None, seconds=None, timedOut=False,
            ), reachability=None)
        metadata = json.loads(footer)
        if metadata.get('format') != 'tbv-verification-log-v1':
            raise ValueError('unsupported verification log format: ' + str(log))
        for key in ('modelCheck', 'reachability'):
            phase = metadata[key]
            if phase is None:
                continue
            for channel in ('stdout', 'stderr'):
                span = phase[channel]
                start, length = span['offset'], span['length']
                # 位置は本文の範囲内だけを許し，壊れた対応表でfooter等を出力として読まない．
                if not (isinstance(start, int) and isinstance(length, int) and 0 <= start <= start + length <= len(body)):
                    raise ValueError('invalid output span in verification log: ' + str(log))
                phase[channel] = raw[start:start + length].decode('utf-8', errors='replace')
        return metadata

    # 保存済みの旧9ファイルは変更せず，読取り時だけ新形式と同じキーへそろえる．
    def legacy_phase(prefix):
        exit_text = _read_text(Path(str(model) + prefix + '.exitcode')).strip()
        return dict(
            stdout=_read_text(Path(str(model) + prefix + '.stdout')),
            stderr=_read_text(Path(str(model) + prefix + '.stderr')),
            exitcode=int(exit_text) if exit_text else None,
            seconds=None, timedOut=exit_text == '124',
        )
    check = legacy_phase('')
    seconds = _read_text(Path(str(model) + '.seconds')).strip()
    if seconds:
        check['seconds'] = float(seconds)
    timing = _read_text(Path(str(model) + '.timing'))
    match = re.search(r'real\s+(\d+)m\s*([0-9.]+)s', timing)
    if match:
        check['seconds'] = int(match[1]) * 60 + float(match[2])
    stats = legacy_phase('.stats')
    return dict(format='legacy-sidecars', modelCheck=check, reachability=stats)


def _analysis_context(model):
    """対応表から有限仮定と未対応事項を取得し，SMVとのhash対応も確認する．"""
    candidates = [Path(str(model) + '.map.json'), model.with_suffix('.map.json')]
    mapping = next((path for path in candidates if path.is_file()), None)
    if mapping is None:
        return dict(status='unavailable', warning='対応表がないため，解析条件と未対応事項は確認できません．')
    try:
        manifest = json.loads(mapping.read_text())
        expected = manifest.get('modelSha256')
        if expected and expected != hashlib.sha256(model.read_bytes()).hexdigest():
            return dict(status='mismatch', sourceMap=str(mapping), warning='対応表とSMVのhashが一致しません．対応表の条件をこのモデルの根拠には使えません．')
        scenario = manifest.get('scenario', {})
        # 実際の遷移に使ったcontextの代表値を示し，YAMLの観測事実と区別する．
        keys = ('id', 'source', 'event', 'activity', 'branch', 'scope', 'repository', 'fork',
                'defaultBranch', 'baseBranch', 'defaultBranchWorkflow', 'sendWriteTokens',
                'runnerOS', 'runnerArch', 'upstreamRun', 'upstreamName')
        return dict(
            status='matched' if expected else 'legacy-unbound', sourceMap=str(mapping),
            inputMode=manifest.get('inputMode', 'not-recorded'),
            verdict=manifest.get('verdict', 'unknown/unsupported'),
            analysisScope=manifest.get('analysisScope', '有限モデルの条件は対応表を参照してください．'),
            assumptions=manifest.get('assumptions', []),
            automaticAssumptions=manifest.get('automaticAssumptions', []),
            defaultContext=scenario.get('defaultContext'),
            runContexts=[{key: run[key] for key in keys if key in run} for run in scenario.get('runs', [])],
            diagnostics=manifest.get('diagnostics', []),
            externalConditions=manifest.get('externalConditions'),
            properties=manifest.get('properties',{}),
            warning=None if expected else '旧対応表にはSMVのhashがなく，モデルとの一致は未確認です．',
        )
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        # 診断情報を読めない場合もNuSMV単独の検査は行い，条件確認失敗を隠さない．
        return dict(status='unreadable', sourceMap=str(mapping), warning='対応表を読み取れません: ' + str(exc))


def verify(model, executable='NuSMV', timeout=120, *, condition_property='no_bad_authority', check_conditions=True):
    """CTL検査と状態列挙を実行し，1つのログと2つの終了コードを返す．"""
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    model = Path(model).resolve()
    if not model.is_file():
        raise FileNotFoundError(model)
    log = Path(str(model) + '.log')
    # 同じmodelに旧結果が残る場合も，混在・上書きを避けて新しい保存先を要求する．
    for path in (log, *(Path(str(model) + suffix) for suffix in _LEGACY_SUFFIXES)):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f'result already exists; use a new output directory: {path}')
    metadata = dict(
        format='tbv-verification-log-v1', model=str(model), timeoutSeconds=timeout,
        invocation=[sys.executable, *sys.argv], startedAt=_now(),
        modelCheck=None, reachability=None,
    )
    metadata['analysisContext'] = _analysis_context(model)
    context = metadata['analysisContext']
    if context.get('verdict') == 'unknown/unsupported':
        print('注意: unknown/unsupportedです．CTLのtrueを安全判定に使わず，ログの未対応事項を確認してください．', flush=True)
    elif context.get('inputMode') == 'yaml-only':
        print('解析条件: YAMLから自動構成した有限仮定の下で検証します．仮定はログに記録します．', flush=True)
    if context.get('warning'):
        print('注意: ' + context['warning'], flush=True)
    started = time.monotonic()
    # 無bufferのbinary fileにし，子プロセスが書いた後のbyte位置も同じfile descriptorで取得する．
    with log.open('xb', buffering=0) as stream:
        _write(stream, f'NuSMV 検証ログ\nモデル: {model}\n開始: {metadata["startedAt"]}\n起動: {shlex.join(metadata["invocation"])}\n1回の時間上限（秒）: {timeout}\n')
        # propertyを読む前に，適用条件と未対応を同じログ内で確認できるようにする．
        _write(stream, '\n=== 解析条件・仮定・未対応事項 ===\n')
        if context.get('verdict') == 'unknown/unsupported':
            _write(stream, '注意: unknown/unsupported．以下のCTL結果だけから安全とは判断できません．\n')
        _write(stream, '到達性は記録した有限仮定の下での結果であり，実際の実行の観測ではありません．\n')
        _write(stream, json.dumps(context, ensure_ascii=False, indent=2) + '\n')
        check = _run_phase(stream, 'CTL検査・遷移全域性', [executable, '-ctt', str(model)], timeout)
        metadata['modelCheck'] = check
        if check['exitcode'] == 0:
            # 日本語パスを\\uへ変換せず，NuSMVへUTF-8のパスをそのまま渡す．
            commands = f'read_model -i {json.dumps(str(model), ensure_ascii=False)}\ngo\ncompute_reachable\nprint_reachable_states\nquit\n'
            with tempfile.TemporaryDirectory(prefix='tbv-nusmv-') as temporary:
                command_file = Path(temporary) / 'commands'
                command_file.write_text(commands)
                stats = _run_phase(stream, '到達状態数の計算', [executable, '-source', str(command_file)], timeout, commands)
            # NuSMVは内部エラーでも0で終わる場合があるので，到達状態数の実出力も確認する．
            # 本文はstdoutの範囲だけを読み，stderrや後続のmetadataをproperty結果へ混ぜない．
            with log.open('rb') as saved:
                saved.seek(stats['stdout']['offset'])
                stdout = saved.read(stats['stdout']['length']).decode('utf-8', errors='replace')
            counts = re.findall(r'reachable states:\s*([^\n]+)', stdout, re.I)
            try:
                nonempty = bool(counts) and float(counts[0].split()[0]) > 0
            except (ValueError, IndexError):
                nonempty = False
            stats['nonemptyReachableSet'] = nonempty
            if stats['exitcode'] == 0 and not nonempty:
                stats['exitcode'] = 1
                stats['diagnostic'] = 'NuSMV returned 0 but no positive reachable-state count was produced.'
                _write(stream, '\n検証支援の診断: ' + stats['diagnostic'] + '\n判定用終了コード: 1\n')
            metadata['reachability'] = stats
        if (metadata['analysisContext'].get('externalConditions') and check['exitcode']==0
                and metadata['reachability'] and metadata['reachability']['exitcode']==0):
            from workflow_model_v2.conditional_verification import explain
            with log.open('rb') as saved:
                saved.seek(check['stdout']['offset'])
                primary_stdout=saved.read(check['stdout']['length']).decode('utf-8',errors='replace')
            report=explain(model,metadata['analysisContext'],primary_stdout,stream,_run_phase,_write,
                           executable,timeout,condition_property,check_conditions)
            report['validation']=dict(ctlExitcode=check['exitcode'],stateEnumerationExitcode=metadata['reachability']['exitcode'],nonemptyReachableSet=metadata['reachability']['nonemptyReachableSet'],totalTransitionRelation='The transition relation is total: No deadlock state exists' in primary_stdout)
            if not report['validation']['totalTransitionRelation']:
                report['reachability']='incomplete'
                report['necessaryConditionsWithinModel']=[]
            metadata['conditionalReport']=report
            _write(stream,'\n=== 外部条件と到達性の報告 ===\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(f'Property: {report["property"]}; Reachability: {report["reachability"]}; Boolean checks: {report["booleanCheckStatus"]}',flush=True)
            for observed in report['witnessConditions']:
                if observed['name'] in {'event','activity','fork','headBranch','baseBranchName','tokenPolicy','sendWriteTokens'}:
                    print(f'  反例中の値（必要条件とは未確認）: {observed["owner"]}.{observed["name"]}={observed["value"]}',flush=True)
            for necessary in report['necessaryConditionsWithinModel']:
                print(f'  有限モデル内の必要条件: {necessary["owner"]}.{necessary["name"]}={necessary["value"]}',flush=True)
        elif metadata['analysisContext'].get('externalConditions'):
            # CTLまたは状態列挙が未完了なら，true/falseを補わず検証失敗の軸だけを残す．
            metadata['conditionalReport']=dict(property=condition_property,reachability='incomplete',
                validation=dict(modelCheck=check,reachability=metadata['reachability']),
                coverage=metadata['analysisContext']['externalConditions'].get('coverage',{}),
                unsupported=[item for item in context.get('diagnostics',[]) if item['status']=='unsupported'],
                unknown=[item for item in context.get('diagnostics',[]) if item['status']=='unknown'],
                witnessConditions=[],necessaryConditionsWithinModel=[],booleanChecks=[],booleanCheckStatus='incomplete-primary-verification')
            _write(stream,'\n=== 外部条件と到達性の報告 ===\n'+json.dumps(metadata['conditionalReport'],ensure_ascii=False,indent=2)+'\n')
        metadata['finishedAt'] = _now()
        metadata['totalSeconds'] = time.monotonic() - started
        stats = metadata['reachability']
        stats_code = stats['exitcode'] if stats else None
        _write(stream, f'\n=== 全体の終了状態 ===\nCTL検査: {check["exitcode"]}\n到達状態数の計算: {stats_code}\n合計実行時間（秒）: {metadata["totalSeconds"]:.6f}\n')
        # 集計用の情報は同じログの末尾へ置き，別のJSONや時間fileを作らない．
        stream.write(_METADATA_MARKER)
        _write(stream, json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    print(f'NuSMV exit={check["exitcode"]}, stats={stats_code}; log={log}', flush=True)
    return check['exitcode'], stats_code


def main():
    """単一SMVまたはinventory全体を検証するCLI．"""
    parser = argparse.ArgumentParser(description=__doc__)
    # Path/floatは引数の文字列を変換し，存在や正の時間はverifyで別途確認する．
    parser.add_argument('target', type=Path, help='one .smv file, or an evaluation directory containing inventory.json')
    parser.add_argument('--timeout', type=float, default=120, help='seconds allowed per NuSMV invocation; timeout is incomplete, not safe')
    parser.add_argument('--property',dest='condition_property',default='no_bad_authority',help='named AG !target property for external-condition analysis')
    parser.add_argument('--no-condition-checks',action='store_true',help='decode witness only; do not establish Boolean necessary conditions')
    args = parser.parse_args()
    try:
        if args.target.is_dir():
            inventory = json.loads((args.target / 'inventory.json').read_text())
            failed = False
            for item in inventory:
                code, stats_code = verify(args.target / item['model'], timeout=args.timeout,condition_property=args.condition_property,check_conditions=not args.no_condition_checks)
                intentional = item['id'] == 'legacy-nusmv-invalid-input'
                failed |= code == 0 if intentional else code != 0 or stats_code != 0
            return int(failed)
        code, stats_code = verify(args.target, timeout=args.timeout,condition_property=args.condition_property,check_conditions=not args.no_condition_checks)
        return int(code != 0 or stats_code != 0)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
