#!/usr/bin/env python3
# 評価用の入力とモデルを作り，別途実行したNuSMVの結果を集計する．
# generateは生成，summarizeは照合・反例の対応付けを担当する．
# 既存の評価結果は上書きしない．
"""共通backendを使う評価入力の生成と，NuSMV結果の照合を担当する．

入力はgenerate/summarizeの処理指定と，新しい評価出力先である．
generateは既存workflowやfixtureから入力コピー・scenario・SMV・対応表を作り，
対応範囲内ではreference_cacheの独立BFSも実行する．NuSMVは別コマンドで呼ぶ．
summarizeは判定・状態数・反例を読み，保存済み期待値と照合してJSONを出力する．
unsupportedの有無はCTLの真偽と別に記録する．終了コード0だけで安全とはしない．
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import time
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import yaml
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.scenario import default_scenario
from workflow_model_v2.reference_cache import explore_cache
from workflow_model_v2.trace_mapper import map_extended_trace, validate_sources
from workflow_model_v2.verification import read_verification
from test_workflow_model_v2_extended import cache_pair, context

def dump(path, value):
    """Python値を整形したJSON fileとして保存する．"""
    # 保存先の親階層を必要なだけ作り，JSONの種類ごとに専用pathへ出力できるようにする．
    path.parent.mkdir(parents=True, exist_ok=True)
    # 日本語をそのまま保持してJSONを整形し，末尾改行を付けて保存する．
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

# 新しい意味規則のfixtureも共通compilerへ渡す．
# 期待propertyは具体的な出力値と到達性を含める．
def generate_dataflow_cases(out):
    """固定fixtureを入力へ展開し，具体値とラベルのCTL probeを追加する．"""
    from workflow_model_v2.scenario import default_scenario
    from workflow_model_v2.transition import atom, symbol
    records = []
    # 保存済みのdataflow fixtureを読み，各caseのworkflow・scenario・期待値を展開する．
    for spec in json.loads((ROOT / 'tests/fixtures/dataflow-cases.json').read_text())['cases']:
        started = time.perf_counter()
        target = out / 'dataflow' / spec['id']
        # caseごとの新規directoryを要求し，既存の評価結果へ混在させない．
        target.mkdir(parents=True, exist_ok=False)
        paths = []
        for index, raw in enumerate(spec['workflows']):
            path = target / f'workflow-{index}.yml'
            # fixture内の辞書を入力コピーのYAMLへ保存し，元fixtureは変更しない．
            path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
            paths.append(path)
        scenario = default_scenario(paths)
        extra = spec.get('scenario', {})
        # runs以外の追加scenarioを反映し，run別の設定は次のzipで対応を保って更新する．
        scenario.update({k: v for k, v in extra.items() if k != 'runs'})
        # 既定runのcontextと追加指定を同じ順で組にし，指定があるrunだけを更新する．
        for context, update in zip(scenario['runs'], extra.get('runs', [])):
            context.update(update)
        # 値と依存の評価ではstep成否を成功・取消なし・Cache I/O成功へ固定する．
        scenario.update(operationOutcome=True, cancelRequests=False, cacheIO=True)
        if spec.get('snapshotFiles'):
            snapshot = target / 'snapshot'
            snapshot.mkdir()
            for name, value in spec['snapshotFiles'].items():
                path = snapshot / name
                # 保存先の親階層を必要なだけ作り，JSONの種類ごとに専用pathへ出力できるようにする．
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            scenario['runs'][0]['repositorySnapshot'] = str(snapshot)
        # 入力コピーを共通backendへ渡し，SMV本体と元YAMLへの対応表を同時に得る．
        text, mapping = compile_workflows(paths, scenario)
        # 期待propertyをコピーし，今回追加するprobeの期待値も同じ表へ入れる．
        expected = dict(spec.get('expectedProperties', {}))
        # outputが指定した値になることを検査するprobeを，識別番号つきで追加する．
        for index, probe in enumerate(spec.get('probeEquals', [])):
            job = next((j for j in mapping['jobs'] if j['original'] == probe['job']))
            # probeのJob・step・output名に一致するSMV記号を対応表から探す．
            output = next((o for o in mapping['outputSymbols'] if o['job'] == job['id'] and o['step'] == probe['step'] and (o['output'] == probe['output'])))
            outcome = symbol('outcome', job['id'] + ':' + probe['step']) + '_conclusion'
            # step成功ならoutputが期待値という含意を作り，値の等しさを全到達状態で検査する．
            expr = f"AG ({outcome} = success -> {output['symbol']} = {atom(probe['value'])})"
            # EFは「少なくとも1経路でこの状態に到達できるか」．
            # AGの含意が未実行stepだけで成立するのを見落とさない．
            reach = f'EF {outcome} = success'
            text += f'\nCTLSPEC NAME probe_equal_{index} := {expr};\nCTLSPEC NAME probe_reachable_{index} := {reach};\n'
            expected[expr] = 'true'
            # 成功状態の存在も期待値へ加え，stepが実行不能なため含意だけが成立する場合を検出する．
            expected[reach] = 'true'
        # 同じoutputのorigin/taint/data/controlが期待通りか，値の等価性と独立に検査する．
        for index, probe in enumerate(spec.get('probeLabels', [])):
            job = next((j for j in mapping['jobs'] if j['original'] == probe['job']))
            label = next((d for d in mapping['dataflowLabels'] if d['kind'] == 'output' and d['job'] == job['id'] and (d['identity'] == probe['step'] + ':' + probe['output'])))
            outcome = symbol('outcome', job['id'] + ':' + probe['step']) + '_conclusion'
            for name in ('origin', 'taint', 'dataDependency', 'controlDependency'):
                if name not in probe:
                    continue
                expr = f'AG ({outcome} = success -> {label[name]} = {str(probe[name]).upper()})'
                text += f'CTLSPEC NAME probe_{name}_{index} := {expr};\n'
                expected[expr] = 'true'
        # 必須診断名から実際の診断名を引き，未対応条件を見落としていないか確認する．
        missing = set(spec.get('expectedDiagnostics', [])) - {d['code'] for d in mapping['diagnostics']}
        # 必須診断が欠けたcaseは評価成功にせず，生成または集計を失敗として扱う．
        if missing:
            raise AssertionError((spec['id'], 'missing diagnostics', sorted(missing)))
        (target / 'model.smv').write_text(text)
        # probeを追加した場合も，ログに表示する条件がこのSMVに対応することをhashで照合する．
        mapping['modelSha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        dump(target / 'model.map.json', mapping)
        dump(target / 'scenario.json', scenario)
        records.append(dict(
            id='flow-' + spec['id'],
            engine='unified',
            model=str((target / 'model.smv').relative_to(out)),
            expected=None,
            expectedProperties=expected,
            generationSeconds=time.perf_counter() - started,
            expectedDiagnostics=spec.get('expectedDiagnostics', []),
            verdict=mapping['verdict']
        ))
    return records

# 入力コピー・scenario・SMV・対応表・参照探索の結果を，新しい出力先へ保存する．
def generate(out):
    """既存回帰・mutation・追加対照・TanStackの評価一式を生成する．"""
    # 既存の結果がある出力先を拒否し，保存済みのモデル・ログを上書きしない．
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f'output directory must be empty: {out}')
    out.mkdir(parents=True, exist_ok=True)
    records = []
    producer = ROOT / '.github/workflows/artifact-a1-pr-producer.yml'
    for case, consumer in [
        ('A1', 'artifact-a1-unsafe-consumer.yml'),
        ('A2', 'artifact-a2-safe-consumer.yml'),
        ('A3', 'artifact-a3-download-only-consumer.yml'),
        ('A4', 'artifact-a4-no-authority-consumer.yml'),
        ('A5', 'artifact-a5-attest-consumer.yml')
    ]:
        p = ROOT / '.github/workflows/artifact-a5-attest-producer.yml' if case == 'A5' else producer
        c = ROOT / '.github/workflows' / consumer
        # 入力コピーを共通backendへ渡し，SMV本体と元YAMLへの対応表を同時に得る．
        text, mapping = compile_workflows([p, c],default_scenario([p,c]))
        target = out / 'baseline' / case
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix('.smv').write_text(text)
        dump(target.with_suffix('.map.json'), mapping)
        records.append(dict(
            id=case,
            model=str(target.with_suffix('.smv').relative_to(out)),
            expected=None,
            engine='unified'
        ))
    from workflow_model_v2 import mutation_matrix
    # 対照生成の出力先を今回の評価directory配下へ設定し，通常の入力を変更しない．
    mutation_matrix.OUT = out / 'mutations'
    # 登録された対照入力とモデルを生成し，続けてmatrix一覧を共通inventoryへ取り込む．
    mutation_matrix.main()
    for record in json.loads((mutation_matrix.OUT / 'matrix.json').read_text()):
        records.append(dict(
            id='legacy-' + record['mutation'],
            model='mutations/' + record['model'],
            expected=None,
            engine='unified'
        ))
    # 後のreadが遮断されても，それ以前の利用履歴が消えないことを対照で確認する．
    audit = out / 'audit-history'
    audit.mkdir(parents=True, exist_ok=True)
    a2 = (ROOT / '.github/workflows/artifact-a2-safe-consumer.yml').read_text()
    earlier = '      - name: Read before verification\n        env:\n          ARTIFACT_FILE: ${{ runner.temp }}/trust-boundary-artifact/payload.txt\n        run: |\n          request=$(tr -d \'\\r\\n\' < "$ARTIFACT_FILE")\n\n'
    a2 = a2.replace(
        '      - name: Verify artifact before use\n',
        earlier + '      - name: Verify artifact before use\n'
    )
    (audit / 'consumer.yml').write_text(a2)
    # 入力コピーを共通backendへ渡し，SMV本体と元YAMLへの対応表を同時に得る．
    text, mapping = compile_workflows([producer, audit / 'consumer.yml'],default_scenario([producer,audit / 'consumer.yml']))
    text += '\nCTLSPEC AG !(integrity_blocked & artifact_dependent_use)\n'
    (audit / 'model.smv').write_text(text)
    # 履歴監査用CTLを追加した最終本文と，ログに表示する条件の対応を保つ．
    mapping['modelSha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    dump(audit / 'model.map.json', mapping)
    records.append(dict(
        id='audit-use-before-later-block',
        engine='unified',
        model='audit-history/model.smv',
        expected=None
    ))
    # 移植の確認にも，共通の変換器・scheduler・object登録表を使う．
    a2 = (ROOT / '.github/workflows/artifact-a2-safe-consumer.yml').read_text()
    a1 = (ROOT / '.github/workflows/artifact-a1-unsafe-consumer.yml').read_text()
    a5p = (ROOT / '.github/workflows/artifact-a5-attest-producer.yml').read_text()
    a5c = (ROOT / '.github/workflows/artifact-a5-attest-consumer.yml').read_text()
    cases = []
    renamed = a2.replace('expected_digest', 'trusted_hash').replace('actual_digest', 'computed_hash').replace('verified=true', 'accepted=true').replace('verified=false', 'accepted=false').replace('"$verified"', '"$accepted"')
    cases.append((
        'digest-renamed-symbols',
        producer.read_text(),
        renamed,
        {
            'AG !bad_simulated_path': 'true',
            'AG !post_verification_use': 'false',
            'AG !verification_read': 'false'
        },
        False
    ))
    unguarded = a2.replace("if: steps.verify-artifact.outputs.verified == 'true'", 'if: always()')
    cases.append((
        'digest-unguarded-use',
        producer.read_text(),
        unguarded,
        {'AG !bad_simulated_path': 'false', 'AG !post_integrity_mismatch_use': 'false'},
        False
    ))
    second = producer.read_text() + '      - uses: actions/upload-artifact@v4\n        with: {name: second, path: .research-artifact-input/payload.txt}\n'
    other = a2.replace(
        "if: steps.verify-artifact.outputs.verified == 'true'",
        'if: false'
    ) + '      - uses: actions/download-artifact@v4\n        with:\n          name: second\n          path: /tmp/runner/unverified\n          run-id: ${{ github.event.workflow_run.id }}\n          github-token: ${{ secrets.GITHUB_TOKEN }}\n      - env: {INPUT_FILE: /tmp/runner/unverified/payload.txt}\n        run: |\n          value=$(tr -d \'\\r\\n\' < "$INPUT_FILE")\n'
    cases.append((
        'verify-other-file',
        second,
        other,
        {
            'AG !post_verification_use': 'true',
            'AG !artifact_dependent_use': 'false',
            'AG !post_integrity_mismatch_use': 'true'
        },
        False
    ))
    overwritten = a2.replace(
        '      - name: Use only the verified artifact',
        "      - run: echo 'publish=true' > /tmp/runner/trust-boundary-artifact/payload.txt\n      - name: Use only the verified artifact"
    )
    cases.append((
        'overwrite-after-verify',
        producer.read_text(),
        overwritten,
        {
            'AG !post_verification_use': 'true',
            'AG !artifact_dependent_use': 'true',
            'AG !bad_simulated_path': 'true'
        },
        False
    ))
    cases.append((
        'reader-declared-first',
        a5p,
        a5c,
        {'AG !bad_simulated_path': 'false', 'AG !artifact_dependent_use': 'false'},
        True
    ))
    inline_read = '          if [ "$verified" = "true" ]; then\n            inspected=$(tr -d \'\\r\\n\' < "$ARTIFACT_FILE")\n          else\n            inspected=\'blocked\'\n          fi\n\n'
    in_step = a2.replace(
        "          printf 'verified=%s",
        inline_read + "          printf 'verified=%s",
        1
    ).replace("if: steps.verify-artifact.outputs.verified == 'true'", 'if: false')
    cases.append((
        'same-step-verified-use',
        producer.read_text(),
        in_step,
        {
            'AG !post_verification_use': 'false',
            'AG !post_integrity_mismatch_use': 'true',
            'AG !bad_simulated_path': 'true'
        },
        False
    ))
    for name, pp, cc, expected_properties, reversed_order in cases:
        target = out / 'migration' / name
        target.mkdir(parents=True, exist_ok=True)
        paths = [target / 'producer.yml', target / 'consumer.yml']
        paths[0].write_text(pp)
        paths[1].write_text(cc)
        scenario = default_scenario(paths)
        if reversed_order:
            paths.reverse()
            scenario['runs'].reverse()
        # 入力コピーを共通backendへ渡し，SMV本体と元YAMLへの対応表を同時に得る．
        text, mapping = compile_workflows(paths, scenario)
        (target / 'model.smv').write_text(text)
        dump(target / 'model.map.json', mapping)
        dump(target / 'scenario.json', scenario)
        records.append(dict(
            id='migration-' + name,
            engine='unified',
            model=str((target / 'model.smv').relative_to(out)),
            expected=None,
            expectedProperties=expected_properties
        ))
    p, c, scenario = cache_pair()
    variants = []

    def add(name, pp=p, cc=c, ss=scenario, expected=True, reference=True):
        """入力と条件の対照をvariantsへ登録する．"""
        variants.append((name, pp, cc, copy.deepcopy(ss), expected, reference))
    add('cache-prefix-unsafe')
    add('cache-key-mismatch', cc=c.replace('linux-pkg-', 'different-'), expected=False)
    s = copy.deepcopy(scenario)
    s['runs'][0].update(event='pull_request', scope='refs/pull/7/merge')
    add('cache-pr-scope-isolation', pp=p.replace('pull_request_target', 'pull_request'), ss=s, expected=False)
    s = copy.deepcopy(scenario)
    s['runs'][1]['cacheVersion']['data'] = 'version2'
    add('cache-version-mismatch', ss=s, expected=False)
    s = copy.deepcopy(scenario)
    s['runs'][1]['repository'] = 'other/repository'
    add('cache-repository-mismatch', ss=s, expected=False)
    add(
        'cache-write-disabled',
        pp=p.replace(
            '      - uses: actions/cache/save@v4',
            '      - if: false\n        uses: actions/cache/save@v4'
        ),
        expected=False
    )
    add(
        'cache-read-disabled',
        cc=c.replace('      - id: cache', '      - if: false\n        id: cache'),
        expected=False
    )
    add('cache-no-authority', cc=c.replace('contents: write', 'contents: read'), expected=False)
    add('cache-no-stage', cc=c.replace('      - run: git add data\n', ''), expected=False)
    s = copy.deepcopy(scenario)
    s['cacheIO'] = False
    add('cache-save-warning-no-object', ss=s, expected=False)
    add('cache-exact-hit', pp=p.replace('linux-pkg-new', 'linux-pkg-current'))
    add(
        'cache-output-guard',
        cc=c.replace(
            '      - run: git add data',
            "      - if: steps.cache.outputs.cache-hit == 'false'\n        run: git add data"
        )
    )
    add(
        'cache-output-contradiction',
        cc=c.replace(
            '      - run: git add data',
            "      - if: steps.cache.outputs.cache-hit == 'false' && steps.cache.outputs.cache-hit != 'false'\n        run: git add data"
        ),
        expected=False
    )
    recovered = c.replace(
        '      - run: git add data',
        '      - run: exit 1\n      - if: failure()\n        run: git add data'
    ).replace('      - run: git push', '      - if: always()\n        run: git push')
    add('status-failure-always-recovery', cc=recovered)
    add(
        'status-default-skip-after-failure',
        cc=c.replace('      - run: git add data', '      - run: exit 1\n      - run: git add data'),
        expected=False
    )
    add(
        'status-cancelled-cleanup',
        cc=c.replace(
            '      - run: git add data',
            '      - if: cancelled()\n        run: git add data'
        ).replace('      - run: git push', '      - if: cancelled()\n        run: git push'),
        reference=False
    )
    add('cache-post-save', pp=p.replace('actions/cache/save@v4', 'actions/cache@v4'))
    add(
        'cache-post-job-failure',
        pp=p.replace('actions/cache/save@v4', 'actions/cache@v4') + '      - run: exit 1\n',
        expected=False
    )
    add(
        'cache-post-continue-on-error',
        pp=p.replace('actions/cache/save@v4', 'actions/cache@v4') + '      - run: exit 1\n        continue-on-error: true\n'
    )
    s = copy.deepcopy(scenario)
    s['cachePolicy'] = 'github-current-2026-10-22'
    add('cache-current-default-read', ss=s, expected=False)
    add('cache-current-explicit-write', pp=p.replace('permissions:', 'cache-mode: write\npermissions:'), ss=s)
    multiple = p + "      - run: echo 'clean' > data\n      - uses: actions/cache/save@v4\n        with: {key: linux-pkg-clean, path: data}\n"
    s = copy.deepcopy(scenario)
    s.update(operationOutcome=True, cancelRequests=False, cacheIO=True)
    add('cache-newest-prefix-is-clean', pp=multiple, ss=s, expected=False)
    add(
        'cache-exact-before-newest-prefix',
        pp=multiple,
        cc=c.replace('key: linux-pkg-current', 'key: linux-pkg-new'),
        ss=s
    )
    add('unsupported-shell', cc=c.replace('cat data', 'eval input'), expected=None, reference=False)
    add(
        'unsupported-action',
        pp=p.replace('actions/cache/save@v4', 'actions/cache/save@unregistered'),
        expected=None,
        reference=False
    )
    add(
        'unsupported-cache-key-collation',
        pp=p.replace('linux-pkg-new', 'Linux-pkg-new'),
        expected=None,
        reference=False
    )
    add(
        'static-matrix',
        pp=p.replace(
            '    steps:',
            '    strategy:\n      fail-fast: false\n      matrix: {platform: [linux, windows]}\n    steps:'
        ),
        reference=False
    )
    for name, pp, cc, ss, expected, reference in variants:
        target = out / 'extended' / name
        target.mkdir(parents=True, exist_ok=True)
        paths = [target / 'producer.yml', target / 'consumer.yml']
        for path, source in zip(paths, [pp, cc]):
            path.write_text(source)
        dump(target / 'scenario.json', ss)
        start = time.monotonic()
        # 入力コピーを共通backendへ渡し，SMV本体と元YAMLへの対応表を同時に得る．
        text, mapping = compile_workflows(paths, ss)
        duration = time.monotonic() - start
        (target / 'model.smv').write_text(text)
        dump(target / 'model.map.json', mapping)
        record = dict(
            id=name,
            engine='unified',
            model=str((target / 'model.smv').relative_to(out)),
            expected=expected,
            generationSeconds=duration,
            stateVariables=mapping['stateVariables'],
            verdict=mapping['verdict']
        )
        if reference:
            start = time.monotonic()
            result = explore_cache(paths, ss)
            result['seconds'] = time.monotonic() - start
            dump(target / 'reference-bfs.json', result)
            record['bfs'] = result
        records.append(record)
    ap = 'name: Artifact Producer\non: pull_request\npermissions: {contents: read}\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n        with: {persist-credentials: false}\n      - id: upload\n        uses: actions/upload-artifact@v4\n        with: {name: payload, path: data}\n'
    ac = 'name: Artifact Consumer\non: push\npermissions: {actions: read, contents: write}\njobs:\n  read:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/download-artifact@v4\n        with:\n          name: payload\n          path: data\n          run-id: writer\n          github-token: ${{ secrets.GITHUB_TOKEN }}\n      - run: cat data\n      - run: git add data\n      - run: git push\n'
    ss = dict(
        cachePolicy='github-historical-2025',
        runs=[
            context(id='writer', event='pull_request', activity='opened', scope='refs/pull/1/merge'),
            context(id='reader', after='writer')
        ]
    )
    artifacts = [
        ('artifact-cross-run', ap, ac, copy.deepcopy(ss), True),
        ('artifact-name-mismatch', ap, ac.replace('name: payload', 'name: other'), copy.deepcopy(ss), False),
        (
            'artifact-run-mismatch',
            ap,
            ac.replace('run-id: writer', 'run-id: unknown'),
            copy.deepcopy(ss),
            False
        ),
        (
            'artifact-token-permission',
            ap,
            ac.replace('actions: read', 'actions: none'),
            copy.deepcopy(ss),
            False
        )
    ]
    ws = copy.deepcopy(ss)
    ws['runs'][1].update(
        event='workflow_run',
        activity='completed',
        upstreamName='Artifact Producer',
        upstreamRun='writer'
    )
    workflow_run = ac.replace(
        'on: push',
        'on:\n  workflow_run:\n    workflows: [Artifact Producer]\n    types: [completed]'
    ).replace(
        '  read:\n',
        "  read:\n    if: github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'pull_request'\n"
    )
    artifacts.append(('artifact-workflow-run-context', ap, workflow_run, ws, True))
    two = ap + "      - run: echo 'clean' > clean\n      - uses: actions/upload-artifact@v4\n        with: {name: clean, path: clean}\n"
    artifacts.append((
        'artifact-two-object-isolation',
        two,
        ac.replace('name: payload', 'name: clean'),
        copy.deepcopy(ss),
        False
    ))
    multi = ac.replace(
        '  read:\n',
        '  upload:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n        with: {ref: pr-head, persist-credentials: false}\n      - uses: actions/upload-artifact@v4\n        with: {name: local, path: data}\n  read:\n    needs: upload\n'
    ).replace('name: payload', 'name: local').replace('          run-id: writer\n', '').replace('          github-token: ${{ secrets.GITHUB_TOKEN }}\n', '')
    ms = copy.deepcopy(ss)
    ms['runs'][1]['checkoutUntrusted'] = True
    artifacts.append(('artifact-multiple-jobs', ap, multi, ms, True))
    outputs = multi.replace(
        '  upload:\n',
        '  upload:\n    outputs: {enabled: "${{ steps.flag.outputs.enabled }}"}\n'
    ).replace(
        '      - uses: actions/upload-artifact@v4\n',
        '      - id: flag\n        run: echo "enabled=true" >> "$GITHUB_OUTPUT"\n      - uses: actions/upload-artifact@v4\n'
    ).replace('    needs: upload\n', "    needs: upload\n    if: needs.upload.outputs.enabled == 'true'\n")
    artifacts.append(('artifact-needs-output', ap, outputs, ms, True))
    package = ac.replace(
        '      - run: cat data\n      - run: git add data\n      - run: git push',
        '      - run: npm publish data'
    )
    ps = copy.deepcopy(ss)
    ps['runs'][1]['externalPackageCredential'] = True
    artifacts.append(('artifact-package-publish', ap, package, ps, True))
    for name, pp, cc, cfg, expected in artifacts:
        target = out / 'extended' / name
        target.mkdir(parents=True, exist_ok=True)
        paths = [target / 'producer.yml', target / 'consumer.yml']
        for path, source in zip(paths, [pp, cc]):
            path.write_text(source)
        dump(target / 'scenario.json', cfg)
        # 入力コピーを共通backendへ渡し，SMV本体と元YAMLへの対応表を同時に得る．
        text, mapping = compile_workflows(paths, cfg)
        (target / 'model.smv').write_text(text)
        dump(target / 'model.map.json', mapping)
        records.append(dict(
            id=name,
            engine='unified',
            model=str((target / 'model.smv').relative_to(out)),
            expected=expected,
            stateVariables=mapping['stateVariables'],
            verdict=mapping['verdict']
        ))
    # 実workflowのsnapshotを読む．
    # pnpmや外部Actionを成功する模擬操作へ置き換えない．
    tan = ROOT / 'experiments/public-cases/tanstack'
    for variant in ['pre-incident', 'mitigation']:
        paths = [
            tan / variant / '.github/workflows/bundle-size.yml',
            tan / variant / '.github/workflows/release.yml'
        ]
        if not all((path.exists() for path in paths)):
            candidates = list((tan / variant / '.github/workflows').glob('*'))
            dump(
                out / 'tanstack' / (variant + '-missing.json'),
                {'status': 'not-generated', 'available': [str(p) for p in candidates]}
            )
            continue
        producer_event = 'pull_request_target' if variant == 'pre-incident' else 'pull_request'
        ss = dict(
            cachePolicy='github-historical-2025',
            runs=[
                context(
                    id='pr',
                    event=producer_event,
                    activity='opened',
                    scope='refs/heads/main' if variant == 'pre-incident' else 'refs/pull/1/merge',
                    checkoutUntrusted=True,
                    changedPaths=['packages/example/src/index.ts']
                ),
                context(id='release', after='pr')
            ]
        )
        registry = json.loads((tan / 'composite-contracts.json').read_text())
        ss['compositeContracts'] = {ref: dict(path=str(ROOT / spec['path']), sha256=spec['sha256']) for ref, spec in registry.items()}
        # 入力コピーを共通backendへ渡し，SMV本体と元YAMLへの対応表を同時に得る．
        text, mapping = compile_workflows(paths, ss)
        target = out / 'tanstack' / variant
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix('.smv').write_text(text)
        dump(target.with_suffix('.map.json'), mapping)
        records.append(dict(
            id='tanstack-' + variant,
            engine='unified',
            model=str(target.with_suffix('.smv').relative_to(out)),
            expected=None,
            verdict=mapping['verdict']
        ))
    # 値・依存・hashFiles等のcaseも同じinventoryへ加え，既存回帰と一緒に集計できるようにする．
    records.extend(generate_dataflow_cases(out))
    dump(out / 'inventory.json', records)
    dump(
        out / 'runtime.json',
        dict(
            python=sys.version,
            pyyaml=yaml.__version__,
            platform=platform.platform(),
            machine=platform.machine()
        )
    )
    print('Generated', len(records), 'models')

# CTLの期待値，過去の回帰値，BFS，source map，状態数を照合して集計する．
# 意図した構文エラーの対照は，通常モデルの検証失敗と分ける．
def summarize(out):
    """保存したNuSMV結果を期待値・BFS・source位置と照合し集計する．"""
    if (out / "verification-summary.json").exists():
        raise FileExistsError(f'summary already exists; use a new output directory: {out}')
    inventory = json.loads((out / 'inventory.json').read_text())
    failed = []
    for record in inventory:
        path = out / record['model']
        # 新しい1ファイルのログを優先し，旧sidecarも同じ形で読み取る．
        result = read_verification(path)
        check = result['modelCheck']
        reachability = result['reachability']
        stdout, stderr = check['stdout'], check['stderr']
        exitcode = check['exitcode']
        # NuSMVが表示した式とtrue/falseを辞書へ読む．
        # 終了コードだけでpropertyの成否を決めない．
        properties = dict(re.findall(r'^-- specification (.*?)\s+is (true|false)\s*$', stdout, re.M))
        for name, expression in {
            'no_transfer': 'AG !transfer',
            'no_use': 'AG !dependent_use',
            'no_sink_site': 'AG !sink_site',
            'no_success': 'AG !sink_succeeded',
            'no_bad_authority': 'AG !bad_authority'
        }.items():
            if expression in properties:
                properties[name] = properties[expression]
        intentional = record['id'] == 'legacy-nusmv-invalid-input'
        # 正常終了に加えてproperty表示が存在する場合だけ，verifiedを真にする．
        record.update(exitcode=exitcode, properties=properties, verified=exitcode == 0 and bool(properties))
        # NuSMVの全域性検査の表示を確認し，deadlockによる意図しない不達を見落とさない．
        record['transitionTotal'] = 'The transition relation is total: No deadlock state exists' in stdout
        if record['verified'] and (not record['transitionTotal']):
            failed.append(record['id'] + ':transition-totality')
        if record['id'] in {'A1', 'A2', 'A3', 'A4', 'A5'}:
            saved = json.loads((ROOT / "tests/fixtures/prototype-properties.json").read_text())["cases"][record["id"]]
            # 保存済み回帰値と各propertyを照合し，過去結果自体は書き換えない．
            record["savedPrototypeComparison"] = {name: properties.get(name) == expected for name, expected in saved.items()}
            if not all(record['savedPrototypeComparison'].values()):
                failed.append(record['id'] + ':saved-prototype')
        reference = json.loads((ROOT / 'tests/fixtures/prototype-properties.json').read_text())
        if record['id'] in reference.get('mutations', {}):
            old = reference['mutations'][record['id']]
            adjustments = reference.get('intentionalChanges', {}).get(record['id'], {})
            # 意図した意味論変更が登録されている場合だけafter値を使い，それ以外は保存済み期待値を保つ．
            expected_properties = {name: adjustments.get(name, {}).get('after', value) for name, value in old['properties'].items()}
            record['savedMutationComparison'] = {name: properties.get(name) == value for name, value in expected_properties.items()}
            record['savedMutationExitcodeMatched'] = exitcode == old['exitcode']
            if adjustments:
                record['intentionalSemanticsChanges'] = adjustments
            if not all(record['savedMutationComparison'].values()) or not record['savedMutationExitcodeMatched']:
                failed.append(record['id'] + ':saved-mutation')
        if record.get('expectedProperties'):
            record['expectedPropertiesMatched'] = {name: properties.get(
                name,
                properties.get(re.sub(r'^(EF|EX) \((.*)\)$', r'\1 \2', name))
            ) == value for name, value in record['expectedProperties'].items()}
            if not all(record['expectedPropertiesMatched'].values()):
                failed.append(record['id'] + ':migration-property')
        if record['id'] == 'audit-use-before-later-block':
            record['historyAuditPassed'] = properties.get('AG !(integrity_blocked & artifact_dependent_use)') == 'false' and properties.get('AG !post_integrity_mismatch_use') == 'true'
            if not record['historyAuditPassed']:
                failed.append(record['id'] + ':history-audit')
        # 意図的な不正入力だけは非0終了かつpropertyなしを対照成功とし，通常の失敗と分ける．
        record['syntaxFailureControlPassed'] = intentional and exitcode not in (None, 0) and (not properties)
        if not record['verified'] and (not record['syntaxFailureControlPassed']):
            failed.append(record['id'])
        if record.get('expected') is not None and record['verified']:
            # 危険状態へ到達しないCTLの真偽を読み，危険到達を期待するcaseではfalseと照合する．
            actual = properties.get('no_bad_authority', properties.get('AG !bad_authority'))
            record['expectationMatched'] = actual == ('false' if record['expected'] else 'true')
            if not record['expectationMatched']:
                failed.append(record['id'] + ':expectation')
        # 参照探索が完了したケースだけを照合する．
        # 探索打切りを一致として数えない．
        # 独立BFSが探索を完了したcaseだけを比較し，incompleteやunsupportedを一致へ数えない．
        if record.get('bfs', {}).get('status') == 'complete' and record['verified']:
            comparisons = {}
            for bfsname, prop in [
                ('transfer', 'no_transfer'),
                ('use', 'no_use'),
                ('sink_site', 'no_sink_site'),
                ('success', 'no_success'),
                ('bad_authority', 'no_bad_authority')
            ]:
                actual = properties.get(prop)
                # BFSの到達trueはAG !履歴のfalseに対応するため，真偽を反転して照合する．
                comparisons[bfsname] = actual == ('false' if record['bfs']['reachable'][bfsname] else 'true')
            record['bfsAgreement'] = comparisons
            if not all(comparisons.values()):
                failed.append(record['id'] + ':bfs')
        # 反例の生出力は統合ログに保持し，YAMLとの対応だけを集計結果へまとめる．
        # 記号に対応する候補位置と，actor/PCから得る実行位置を混同しない．
        # model.smvに対応するmodel.map.jsonを探し，記号・診断・sourceの情報を読む．
        mapping_path = path.with_suffix('.map.json')
        mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
        record['diagnostics'] = mapping.get('diagnostics', [])
        record['assumptions'] = mapping.get('assumptions', [])
        record['automaticAssumptions'] = mapping.get('automaticAssumptions', [])
        record['inputMode'] = mapping.get('inputMode')
        record['analysisScope'] = mapping.get('analysisScope')
        record['modelVerdict'] = mapping.get('verdict', 'unknown/unsupported')
        record['stateVariables'] = mapping.get('stateVariables')
        record['generationSeconds'] = mapping.get('generationSeconds', record.get('generationSeconds'))
        # 終了コード124を時間切れとして明示し，安全性の判定と分離する．
        record['timedOut'] = check.get('timedOut', False) or bool(reachability and reachability.get('timedOut', False))
        # 必須診断名から実際の診断名を引き，未対応条件を見落としていないか確認する．
        missing = set(record.get('expectedDiagnostics', [])) - {d['code'] for d in record['diagnostics']}
        # 必須診断が欠けたcaseは評価成功にせず，生成または集計を失敗として扱う．
        if missing:
            failed.append(record['id'] + ':expected-diagnostic')
        if mapping:
            record['sourceValidation'] = validate_sources(mapping)
            if record['sourceValidation']['invalidSpans']:
                failed.append(record['id'] + ':source-map')
        source_symbols = mapping.get('symbols', {})
        # propertyの表示開始ごとに出力を分割し，falseになった性質の反例を個別に扱う．
        traces = re.split(r'(?=^-- specification )', stdout, flags=re.M)
        trace_count = 0
        counterexamples = []
        for trace in traces:
            match = re.match(r'-- specification (.*?) is false', trace)
            # false propertyに対応しない出力断片は，反例fileとして保存しない．
            if not match:
                continue
            trace_count += 1
            mapped = []
            for sym, source in source_symbols.items():
                if re.search(r'\b' + re.escape(sym) + r'\s*=', trace):
                    mapped.append(dict(symbol=sym, source=source))
            # propertyごとのtrace/source-map fileを増やさず，対応情報をsummaryの当該modelへ保存する．
            counterexamples.append(dict(
                property=match.group(1).strip(),
                sourceMap=map_extended_trace(trace, mapping) if mapping.get('modelVersion') == '2-unified-bounded' else mapped,
            ))
        record['counterexamples'] = counterexamples
        record['conditionalReport'] = result.get('conditionalReport')
        # 到達状態の出力と終了状態も，同じ統合ログの別phaseから読む．
        if reachability:
            record['reachableStates'] = re.findall(r'reachable states:\s*([^\n]+)', reachability['stdout'], re.I)
            try:
                record['nonemptyReachableSet'] = bool(record['reachableStates']) and float(record['reachableStates'][0].split()[0]) > 0
            except (ValueError, IndexError):
                record['nonemptyReachableSet'] = False
            record['stateEnumerationSucceeded'] = reachability['exitcode'] == 0 and record['nonemptyReachableSet']
        if record['verified'] and (not record.get('nonemptyReachableSet') or not record.get('stateEnumerationSucceeded')):
            failed.append(record['id'] + ':state-enumeration')
        if check.get('seconds') is not None:
            record['verificationWallSeconds'] = int(check['seconds'])
            record['verificationRealSeconds'] = check['seconds']
        record['verificationLog'] = str(path.relative_to(out)) + '.log' if result['format'] == 'tbv-verification-log-v1' else None
        record['traceCount'] = trace_count
        # unsupportedがある場合は，CTLがtrueでもunknown/unsupportedを優先する．
        record['interpretation'] = 'unknown/unsupported' if record['modelVerdict'] == 'unknown/unsupported' else 'model-checked' if record['verified'] and record.get('stateEnumerationSucceeded') else 'incomplete'
    # caseごとの結果と失敗一覧・正常検証数・構文不正対照数をまとめる．
    summary = dict(
        models=inventory,
        failures=failed,
        total=len(inventory),
        validated=sum((r['verified'] for r in inventory)),
        syntaxControls=sum((r['syntaxFailureControlPassed'] for r in inventory))
    )
    summary['timedOut'] = sum((r['timedOut'] for r in inventory))
    dump(
        out / 'unknown-unsupported-inventory.json',
        {r['id']: dict(
            verdict=r['modelVerdict'],
            diagnostics=r['diagnostics'],
            assumptions=r['assumptions']
        ) for r in inventory if r['diagnostics']}
    )
    dump(
        out / 'report-metrics.json',
        {r['id']: {k: r.get(k) for k in (
            'stateVariables',
            'reachableStates',
            'generationSeconds',
            'verificationRealSeconds',
            'traceCount',
            'timedOut',
            'interpretation'
        )} for r in inventory}
    )
    dump(out / 'verification-summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'models'}, indent=2))
    return 1 if failed else 0
# import時にはCLIを起動せず，このファイルを直接実行した場合だけ入口を呼ぶ．
if __name__ == '__main__':
    # 受け付ける引数を登録する解析器を作り，必須指定・型変換・help表示を管理する．
    parser = argparse.ArgumentParser()
    # generate/summarizeのどちらかだけを位置引数に許し，生成と検証済みログの集計を選ぶ．
    parser.add_argument('mode', choices=['generate', 'summarize'])
    # 評価の出力directoryをPathへ変換し，省略時はコンテナの/outを使う．
    parser.add_argument('--output', type=Path, default=Path('/out'))
    # 指定されたコマンドラインを解析し，各引数をargsの属性へ格納する．
    args = parser.parse_args()
    if args.mode == 'generate':
        generate(args.output)
    else:
        raise SystemExit(summarize(args.output))
