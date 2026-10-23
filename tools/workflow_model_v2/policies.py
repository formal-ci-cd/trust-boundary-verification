# イベントfilter，Cacheの可視性・探索順位，token権限の規則をまとめる．
# 過去の評価用policyと確認時点のpolicyを区別する．
# ここでは状態探索や最終判定を行わない．
"""イベント・Cache・permissions・静的matrixの限定規則を集める．

入力はYAMLの設定とscenarioの外部条件，出力は可否・探索順位・権限値・matrixの組である．
イベントfilterを判定し，Cacheのrepository/scope制約とkey優先順位を求める．
permissionsはjob指定を優先し，未指定の既定値やfork設定が不明ならNoneを返す．
Noneを安全や権限なしと断定せず，compilerが共有の未知変数へ変換する．
policyは登録された版の限定規則であり，未対応patternや動的matrix等は例外にする．
このmoduleだけで状態探索や研究の最終判定を行うことはない．
"""
from __future__ import annotations
import re

class UnsupportedPolicy(ValueError):
    """選択した規則で表現できない設定を通知する例外．"""
    pass
EVENTS = {
    'pull_request',
    'pull_request_target',
    'push',
    'workflow_run',
    'workflow_dispatch',
    'repository_dispatch',
    'workflow_call',
    'schedule'
}
POLICIES = {'github-historical-2025', 'github-current-2026-10-22'}

def glob(pattern, text):
    """literalと*，**，?の限定patternを正規表現へ変換して照合する．"""
    if not isinstance(pattern, str) or any((c in pattern for c in '[]+\\')):
        raise UnsupportedPolicy('glob supports literal, *, ** and ? only: ' + repr(pattern))
    # 正規表現の断片リストと，patternの読取り位置を別々に初期化する．
    pieces, i = ([], 0)
    # patternの現在位置を進めながら正規表現の断片を作る．
    # **/はdirectoryが0個の場合も一致させる．
    while i < len(pattern):
        # **/はdirectoryが0段の場合も一致するよう，/を含む任意prefix全体を省略可能にする．
        if pattern[i:i + 3] == '**/':
            pieces.append('(?:.*/)?')
            i += 3
        # **は/も含む任意の文字列へ変換し，directoryをまたぐ一致を許す．
        elif pattern[i:i + 2] == '**':
            pieces.append('.*')
            i += 2
        # *は/以外の繰返しへ変換し，directoryをまたがない一致にする．
        elif pattern[i] == '*':
            pieces.append('[^/]*')
            i += 1
        # ?は/以外の1文字だけへ変換する．
        elif pattern[i] == '?':
            pieces.append('[^/]')
            i += 1
        else:
            # literal文字は正規表現の特殊記号として解釈させず，文字そのものへ一致させる．
            pieces.append(re.escape(pattern[i]))
            i += 1
    # 断片を連結してpath全体に照合し，一部だけ一致する場合は採用しない．
    return re.fullmatch(''.join(pieces), text) is not None

# 否定patternを含む指定順に適用する．
# 最後に一致したpatternで採否を更新する．
def ordered_patterns(patterns, text):
    """肯定・否定patternを順に適用して採否を求める．"""
    # patternの列はlistを要求し，文字列を1文字ずつ走査する誤りを防ぐ．
    if isinstance(patterns, str) or not isinstance(patterns, list):
        raise UnsupportedPolicy('filter patterns must be a list')
    # 肯定patternが最低1個あることを要求し，否定だけのinclude指定を未対応にする．
    if not any((isinstance(p, str) and (not p.startswith('!')) for p in patterns)):
        raise UnsupportedPolicy('negative-only or empty include filter')
    # 一致前は不採用とし，後から一致したpatternで採否を更新する．
    matched = False
    # 指定順にpatternを適用し，後の否定・再包含の効果を残す．
    for pattern in patterns:
        # 先頭!を否定指定として分け，照合する本文からは取り除く．
        negative = pattern.startswith('!')
        # このpatternが一致する場合だけ採否を書き換え，一致しなければ直前の結果を保つ．
        if glob(pattern[1:] if negative else pattern, text):
            # 肯定なら採用，否定なら除外へ更新し，最後に一致した指定を優先する．
            matched = not negative
    return matched

def filter_field(config, field, value):
    """branches等のinclude/ignore設定を照合する．"""
    # branchesとbranches-ignore等のキー名を作り，同じfilterのincludeとignoreを区別する．
    positive, negative = (field, field + '-ignore')
    # includeとignoreの併用は対応しないため，片方を無視せず例外にする．
    if positive in config and negative in config:
        raise UnsupportedPolicy(f'{positive} and {negative} cannot coexist')
    if positive in config:
        return ordered_patterns(config[positive], value)
    if negative in config:
        patterns = config[negative]
        if not isinstance(patterns, list) or any((p.startswith('!') for p in patterns)):
            raise UnsupportedPolicy('ignore filter requires non-negated pattern list')
        # ignoreに1個でも一致する値は除外し，どれにも一致しない場合だけ採用する．
        return not any((glob(p, value) for p in patterns))
    return True

# イベント種別・activity・branch/tag/pathのfilterを，明示contextへ適用する．
def trigger(events, context):
    """宣言イベントとscenarioのイベント条件を照合する．"""
    # YAMLに書かれた全イベントを起こすのではなく，scenarioが指定した今回のイベントを照合する．
    event = context['event']
    # 意味規則を持たないイベントは，開始しないと断定せず未対応にする．
    if event not in EVENTS:
        raise UnsupportedPolicy('unmodeled trigger event ' + event)
    # 今回のイベントがonへ宣言されていなければ，このRunは開始しない．
    if event not in events:
        return False
    # filterなしのnull指定を空辞書へそろえ，後段でgetを共通に使えるようにする．
    config = events[event] or {}
    # cron宣言の形は確認するが，配信されたという条件はcontextから受け取る．
    if event == 'schedule':
        if not isinstance(config, list) or not all((isinstance(c, dict) and 'cron' in c for c in config)):
            raise UnsupportedPolicy('invalid schedule')
        # scheduleの配信はscenarioの仮定．
        # cronから時刻を計算したり配信を模擬したりはしない．
        return context['defaultBranchWorkflow']
    if not isinstance(config, dict):
        raise UnsupportedPolicy('event configuration must be mapping')
    # イベントごとに解釈するfilterキーを列挙し，別イベントの構文を流用しない．
    valid_filters = {
        'pull_request': {'types', 'branches', 'branches-ignore', 'paths', 'paths-ignore'},
        'pull_request_target': {'types', 'branches', 'branches-ignore', 'paths', 'paths-ignore'},
        'push': {'branches', 'branches-ignore', 'tags', 'tags-ignore', 'paths', 'paths-ignore'},
        'workflow_run': {'workflows', 'types', 'branches', 'branches-ignore'},
        'workflow_dispatch': {'inputs'},
        'repository_dispatch': {'types'},
        'workflow_call': {'inputs', 'outputs', 'secrets'}
    }[event]
    # 未対応filterが残る場合は，その効果を無視して開始可否を決めない．
    if set(config) - valid_filters:
        raise UnsupportedPolicy('unsupported event keys: ' + ', '.join(sorted(set(config) - valid_filters)))
    # types省略時のactivity候補を，この契約が扱うイベントごとに定義する．
    defaults = {
        'pull_request': ['opened', 'synchronize', 'reopened'],
        'pull_request_target': ['opened', 'synchronize', 'reopened'],
        'workflow_run': ['requested', 'in_progress', 'completed']
    }
    # 明示typesを優先し，省略時だけ登録済みの既定activityを使う．
    types = config.get('types', defaults.get(event))
    if types is not None and (not isinstance(types, list)):
        raise UnsupportedPolicy('event types must be a list')
    # 今回のactivityが許可一覧に含まれない場合は，起動条件を偽にする．
    if types is not None and context['activity'] not in types:
        return False
    if event in {'pull_request', 'pull_request_target', 'push', 'workflow_run'}:
        # pushイベントのtagとbranchをcontext.refTypeで区別し，対応するfilterを選ぶ．
        is_tag = event == 'push' and context['refType'] == 'tag'
        # tagならtags，それ以外ならbranchesを照合対象にする．
        field = 'tags' if is_tag else 'branches'
        # 反対側のfilterだけが指定されたpushを検出するため，もう一方のキー名も作る．
        opposite = 'branches' if is_tag else 'tags'
        if event == 'push' and any((k in config for k in ( opposite, opposite + '-ignore' ))) and (not any((k in config for k in (field, field + '-ignore')))):
            return False
        # 対象branch/tagがinclude/ignoreを通らない場合，このRunは開始しない．
        if not filter_field(config, field, context['branch']):
            return False
    if event in {'push', 'pull_request', 'pull_request_target'} and (not (event == 'push' and context['refType'] == 'tag')):
        if 'paths' in config and 'paths-ignore' in config:
            raise UnsupportedPolicy('paths and paths-ignore cannot coexist')
        # path filterには完全な変更file一覧が必要．
        # 打切りdiffから起動しないと決めつけない．
        if any((k in config for k in ('paths', 'paths-ignore'))):
            # path filterには完全な変更一覧を要求し，不完全なdiffから不達を断定しない．
            if context.get('diffComplete') is not True:
                raise UnsupportedPolicy('path filters need a complete bounded diff; GitHub diff limits/timeouts are not guessed')
            # 外部条件として与えた変更fileの一覧を取り出し，各pathへfilterを適用する．
            files = context['changedPaths']
            if 'paths' in config and (not any((ordered_patterns(config['paths'], f) for f in files))):
                return False
            if 'paths-ignore' in config and (not any((filter_field(config, 'paths', f) for f in files))):
                return False
    if event in {'workflow_dispatch', 'repository_dispatch', 'workflow_run'} and (not context['defaultBranchWorkflow']):
        return False
    # 上流workflow名がworkflows指定に含まれなければ，workflow_runを開始しない．
    if event == 'workflow_run' and context['upstreamName'] not in config.get('workflows', []):
        return False
    return True

# 同じrepository内で，保存scopeを読取り側から参照できるか判定する．
def cache_visible(writer, reader, policy):
    """Cache保存scopeを読取りrunから参照できるか調べる．"""
    # Cache規則の版は明示選択を要求し，登録外のpolicyで可視性・保存可否を推測しない．
    if policy not in POLICIES:
        raise UnsupportedPolicy('cache policy must be explicitly selected')
    # 別repositoryのCacheを同じ共有領域とは見なさず，取得不可にする．
    if writer['repository'] != reader['repository']:
        return False
    # 同じrepository・scopeなら，この可視性規則では取得候補として認める．
    if writer['scope'] == reader['scope']:
        return True
    # 同一scope以外に読める候補として，既定branchのscopeを用意する．
    visible = {reader['defaultBranch']}
    if reader['event'] in {'pull_request', 'pull_request_target'}:
        # PR系の読取りにはbase branchのscopeも追加する．
        visible.add(reader['baseBranch'])
    return writer['scope'] in visible

# 選択policyと実効modeから保存可否を返す．
# 不明な既定設定は推測しない．
def cache_writable(context, policy, mode=None):
    """選択版の規則と実効modeからCache保存可否を求める．"""
    # Cache規則の版は明示選択を要求し，登録外のpolicyで可視性・保存可否を推測しない．
    if policy not in POLICIES:
        raise UnsupportedPolicy('cache policy must be explicitly selected')
    # 履歴版ではcache-modeを持たない保存規則を使い，現行版のmode設定と混ぜない．
    if policy == 'github-historical-2025':
        if mode is not None:
            raise UnsupportedPolicy('cache-mode is not part of historical policy')
        return True
    # current policyは実効modeを必要とする．
    # この関数はrepository/org既定値を推測せず，不明なmodeを例外にする．
    if mode not in {'read', 'write', 'write-only', 'none'}:
        raise UnsupportedPolicy('current policy requires explicit effective cache-mode')
    # 現行modeがwrite/write-onlyの時だけ保存可とし，read/noneでは保存しない．
    return mode in {'write', 'write-only'}

# 主keyの完全一致，主keyのprefix，restore-keysの指定順で探索順位を付ける．
def cache_rank(saved, requested, restore_keys):
    """key検索順を整数の優先順位へ変換する．"""
    if any((not value.isascii() for value in [saved, requested, *restore_keys])):
        raise UnsupportedPolicy('locale-sensitive non-ASCII cache keys require a service matching contract')
    if saved.lower() == requested.lower() and saved != requested or any((saved.lower().startswith(prefix.lower()) and (not saved.startswith(prefix)) for prefix in [requested, *restore_keys])):
        raise UnsupportedPolicy('case-only cache matching is unspecified here; action exact-hit uses locale comparison')
    # 主keyの完全一致を最優先の順位0にする．
    if saved == requested:
        return 0
    # 主keyのprefix一致は完全一致の次の順位1にする．
    if saved.startswith(requested):
        return 1
    # restore-keysを指定順で走査し，先に指定したprefixほど高い優先順位にする．
    for index, prefix in enumerate(restore_keys):
        if saved.startswith(prefix):
            # 主key検索の後になる順位を返し，複数prefixに一致しても最初のものを採用する．
            return 2 + index
    # この関数では一致候補がなかったことをNoneで表し，最悪順位のhitとは区別する．
    return None

# jobの指定をworkflowより優先する．
# 未指定の既定値やfork設定が不明ならNoneを返す．
def permission(raw_workflow, raw_job, context, scope):
    """workflow/job permissionsとfork制限から権限levelを求める．"""
    # jobにpermissionsがあればworkflow値を置き換える．
    # 明示mapping内の省略scopeはnoneとなる．
    declaration = raw_job.get('permissions', raw_workflow.get('permissions'))
    # permissions未指定なら，外部contextのrepository既定権限を参照する．
    if declaration is None:
        # scopeの既定値が不明ならNoneのまま返し，権限なしと決めつけない．
        value = context.get('defaultPermissions', {}).get(scope)
    # read-all/write-allは全scopeに共通のlevelを作り，id-tokenだけはreadをnoneへ落とす．
    elif declaration in ('read-all', 'write-all'):
        value = declaration.split('-')[0]
        if scope == 'id-token' and value == 'read':
            value = 'none'
    # 明示permissions辞書では，書かれていないscopeをnoneとして扱う．
    elif isinstance(declaration, dict):
        # jobがpermissionsを書いた場合，workflowの同名scopeを足し戻さず，省略はnoneにする．
        value = declaration.get(scope, 'none')
    else:
        raise UnsupportedPolicy('invalid permissions declaration')
    if value not in {None, 'none', 'read', 'write'} or (scope == 'id-token' and value == 'read'):
        raise UnsupportedPolicy('invalid permission level')
    # fork PRのwrite低減をsendWriteTokensの明示値で分ける．
    # 不明な場合はNoneを上位へ返す．
    if context['event'] == 'pull_request' and context['fork'] and (value == 'write'):
        if context.get('sendWriteTokens') is False:
            # forkのwrite低減ではwriteをreadへし，readを持たないid-tokenはnoneへする．
            value = 'read' if scope != 'id-token' else 'none'
        elif context.get('sendWriteTokens') is not True:
            # forkのwrite許可設定が未確定なら，実効権限も未知としてcompilerへ返す．
            value = None
    return value

# 元の軸の値を上書きしない組へincludeを適用する．
# 追加組を次のincludeの結合先にはしない．
def expand_matrix(matrix, limit=32):
    """静的な軸，exclude，includeからjobごとの値の組を作る．"""
    from itertools import product
    import json
    if '${{' in json.dumps(matrix):
        raise ValueError('dynamic matrix requires runtime job creation; unsupported')
    # include/exclude以外をmatrixの軸として取り出し，補助設定を直積の次元に混ぜない．
    axes = {k: v for k, v in matrix.items() if k not in {'include', 'exclude'}}
    if any((not isinstance(v, list) or not v for v in axes.values())):
        raise ValueError('matrix axes require nonempty literal arrays')
    size = 1
    for values in axes.values():
        # 軸ごとの候補数を掛け，実際に展開する前にJob数の上限を調べる．
        size *= len(values)
    if size > limit:
        raise ValueError('matrix expansion exceeds configured bound')
    includes, excludes = (matrix.get('include', []), matrix.get('exclude', []))
    if not isinstance(includes, list) or not isinstance(excludes, list) or any((not isinstance(x, dict) for x in includes + excludes)):
        raise ValueError('matrix include/exclude must be lists of mappings')
    if any((set(x) - set(axes) for x in excludes)):
        raise ValueError('matrix exclude references a nonexistent axis')
    # 軸候補の直積から元の組を作る．
    # excludeは元の組に対して条件の部分一致で除外する．
    # productが返す値の組をzipで軸名に対応付け，元のmatrix割当てを辞書にする．
    original = [dict(zip(axes, values)) for values in product(*axes.values())] if axes else []
    # exclude辞書の全キーに一致する割当てだけを除き，部分一致の除外規則を実装する．
    original = [x for x in original if not any((all((x.get(k) == v for k, v in e.items())) for e in excludes))]
    # 元の割当てを個別にコピーし，includeを適用しても比較基準のoriginalを変えない．
    expanded = [dict(x) for x in original]
    # 元の組へ結合できないincludeを，追加Jobの割当てとして別にためる．
    extra = []
    # 各includeを元の組と比較する．
    # includeで追加したextraを後のincludeの結合先にはしない．
    for addition in includes:
        # 一致前は不採用とし，後から一致したpatternで採否を更新する．
        matched = False
        for index, base in enumerate(original):
            if all((k not in base or base[k] == v for k, v in addition.items())):
                expanded[index].update(addition)
                matched = True
        if not matched:
            extra.append(dict(addition))
    result = expanded + extra
    if not axes and (not includes):
        raise ValueError('matrix requires at least one axis or include entry')
    if len(result) > limit:
        raise ValueError('matrix expansion exceeds configured bound')
    return result
