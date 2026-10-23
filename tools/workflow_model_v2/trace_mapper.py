# NuSMVの反例から状態と入力の差分を復元し，actorと実行位置をYAMLのstepへ対応付ける．
# stepの選択，ifの成立，処理の成功は別の事実なので，位置の対応だけで成功を断定しない．
"""NuSMV反例の差分表示を復元し，選ばれたstepをsourceへ戻す．

入力はtrace文字列とmanifest，出力はactor・PC・入力・YAML位置を持つ対応の列である．
Stateの省略値を辞書で保持し，Inputにあるactorをjobへ，PCのs番号をstepへ対応付ける．
対応はstepをscheduleした地点であり，if成立や処理成功を保証するものではない．
validate_sourcesはstep位置の範囲と行頭を調べる．sourceの意味全体やhash同一性は検査しない．
評価スクリプトが対応表と各反例のJSONを保存するために使う．
"""
import re

# 省略された状態値は直前の値を引き継ぎ，遷移で選ばれたjobとPCを対応付ける．
def map_extended_trace(trace, manifest):
    """差分traceを保持しながらactor/PCを元stepへ対応付ける．"""
    # NuSMVが省略した値を前状態から補うため，現在までの状態VARを辞書に保持する．
    state = {}
    # 元traceのState番号を記録し，対応したstepがどの遷移に現れたかを示す．
    state_id = None
    # 現在読んでいるblockがStateかInputかを区別する．
    mode = None
    # actorやoperation_ok等の入力を保持し，入力側の差分も後続blockへ引き継ぐ．
    current_input = {}
    # YAMLのstepへ対応したschedule地点を，traceの順にためる．
    mapped = []
    # Job識別子でmanifestのJobを引ける辞書を作り，actorをsourceへ戻す．
    jobs = {job['id']: job for job in manifest.get('jobs', [])}

    def flush():
        """現在の入力とPCに対応するstepがあれば記録する．"""
        # 現在の入力blockが選んだJobを読み，idleや欠損なら後段で対応なしとして扱う．
        actor = current_input.get('actor')
        # actorに一致するJobのmanifest情報を探し，ない場合はNoneを返す．
        job = jobs.get(actor)
        # idle等で対応Jobがない場合は，YAML stepへの対応を追加しない．
        if job is None:
            return
        # 選ばれたJobの実行位置を復元済み状態から読み，未記録なら空文字を使う．
        pc = state.get('pc_' + actor, '')
        # s0/s1等のstep位置だけを照合し，pendingやJob終端値は対象にしない．
        match = re.fullmatch(r's(\d+)', pc)
        # PCがstep位置でなければ，それをstep実行として報告しない．
        if not match:
            return
        # sの後ろの数字をstep列の0始まりの添字へ変換する．
        index = int(match.group(1))
        # sNがstep列末尾の完了待ち位置なら，存在しない次stepへ対応付けない．
        if index >= len(job['steps']):
            return
        # 元の順序に沿って，このPC位置に対応するstepを取得する．
        step = job['steps'][index]
        # 入力辞書はcopyして保存し，後の差分更新で過去の対応情報が書き換わらないようにする．
        mapped.append(dict(
            state=state_id,
            job=actor,
            step=step['id'],
            pc=pc,
            inputs=dict(current_input),
            source=step['source'],
            meaning='transition scheduled at this source step; if/outcome may skip or fail'
        ))
    # NuSMVは前状態と同じ値を省略する．
    # 辞書をblockごとに初期化せず，表示された差分を上書きする．
    for line in trace.splitlines():
        # State/Inputの見出しと番号を読み，変数代入行との境界を判別する．
        header = re.match(r'\s*-> (State|Input): (.*?) <-', line)
        # 新blockの入口では直前のInputを確定してから，読取りmodeを切り替える．
        if header:
            # Input blockの終端またはtrace末尾で，保持しているactorとPCの対応を1件確定する．
            if mode == 'Input':
                flush()
            # 見出しの種類をState/Inputとして保存し，続く代入先の辞書を選べるようにする．
            mode = header.group(1)
            if mode == 'State':
                state_id = header.group(2)
            continue
        # SMV識別子=値の行だけを読み，traceの説明やproperty表示を状態値へ混ぜない．
        assignment = re.match(r'\s*([A-Za-z_][\w]*) = ([^\s]+)\s*$', line)
        if assignment:
            # Inputなら入力表，それ以外なら状態表を選び，表示された項目だけを上書きする．
            (current_input if mode == 'Input' else state)[assignment.group(1)] = assignment.group(2)
    # Input blockの終端またはtrace末尾で，保持しているactorとPCの対応を1件確定する．
    if mode == 'Input':
        flush()
    return mapped

# 保存されたsource位置が参照先のファイル範囲内にあるか確認する．
def validate_sources(manifest):
    """stepの位置がfileの範囲内で，行頭がstep形式か検査する．"""
    from pathlib import Path
    problems = []
    count = 0
    # 共通backendのJob一覧を優先してsource検査の対象にする．
    groups = manifest.get('jobs', [])
    # 旧形式のmanifestではproducer/consumerのtypedIRから同じ検査対象を取り出す．
    if not groups and 'typedIR' in manifest:
        groups = [manifest['typedIR'][name] for name in ('producer', 'consumer')]
    for job in groups:
        for step in job['steps']:
            source = step['source']
            # sourceが指す元YAMLを読み，保存位置が実際の行範囲内か確認する．
            lines = Path(source['file']).read_text().splitlines()
            count += 1
            # 行番号は1始まりなので，1からファイル行数までの範囲を要求する．
            if not 1 <= source['line'] <= len(lines):
                problems.append(source)
            # Pythonの添字は0始まりなので1を引き，stepの箇条書き行を指すか確認する．
            elif not lines[source['line'] - 1].lstrip().startswith('- '):
                problems.append(source)
    # 検査したstep数と不正な位置一覧を返し，hashやYAML全体の意味まで検証したとはしない．
    return dict(checkedSteps=count, invalidSpans=problems)
