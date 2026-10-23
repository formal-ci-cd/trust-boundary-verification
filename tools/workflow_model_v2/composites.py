# 明示registryに登録されたComposite metadataだけを読取り，内部stepの列へ展開する．
# SHA-256でmetadataの内容を照合する．
# 外部Actionの取得や実行は行わない．
# 展開だけでは保持できない入力・出力・親の条件等には，未対応の診断を残す．
"""明示されたComposite metadataを照合し，内部stepの列へ展開する．

入力は呼出し側step列，YAML node，registryと診断callback，出力はstepとsource位置の列である．
registryにあるfileのSHA-256を照合し，runs.using=compositeを確認して内部stepを再帰展開する．
childをコピーし，idへprefixを付け，呼出し側envより内部envを優先する．
input/outputや親guardの完全なcall frameは実装していないため，該当時は診断を残す．
外部Actionのコード実行やネットワーク取得はせず，compiler._loadから利用される．
"""
from pathlib import Path
import copy
import hashlib
import yaml
from . import frontend as base
from .workflow_loader import WorkflowLoader

# metadataを照合して内部stepを順に展開する．
# 再帰呼出しと深さ上限も確認する．
def expand(steps, node, file, registry, diagnostic, stack=()):
    """登録済みmetadataの内部stepを位置付きで展開する．"""
    # 展開済みstepとsource位置の組を，呼出し順にためる．
    result = []
    # 呼出し側のstepsへ添字を付け，YAMLの同じ位置のnodeと対応付ける．
    for index, raw in enumerate(steps):
        # 元stepのYAML nodeをsourceへ変換し，通常ActionもCompositeも位置を保持する．
        span = base._span(node.value[index] if node else None, file).json()
        # stepが辞書ならusesを読み，そうでない入力はここで勝手に解釈しない．
        ref = raw.get('uses') if isinstance(raw, dict) else None
        # 登録外の参照はCompositeとして展開せず，元stepのまま後段へ渡す．
        if ref not in registry:
            result.append((raw, span))
            continue
        # stackは現在の呼出し列．
        # 同じrefへの再帰と深すぎる展開を止める．
        if ref in stack or len(stack) >= 8:
            raise base.WorkflowError('composite recursion/depth limit: ' + ref)
        # 明示registryからmetadataのパスと期待SHA-256を取り出す．
        entry = registry[ref]
        path = Path(entry['path'])
        # metadataの同じbyte列をhash照合とYAML解析へ使う．
        source = path.read_bytes()
        # 指定したmetadataの内容と登録hashが違えば，別の契約を適用せず変換を停止する．
        if hashlib.sha256(source).hexdigest() != entry['sha256']:
            raise base.WorkflowError('composite metadata hash mismatch: ' + ref)
        # 照合済みmetadataを，workflowと同じ型・重複キー規則で読む．
        metadata = yaml.load(source, Loader=WorkflowLoader)
        # runs.usingがcompositeでない場合は，内部stepsの展開として扱わない．
        if metadata.get('runs', {}).get('using') != 'composite':
            raise base.WorkflowError('registered metadata is not a composite: ' + ref)
        # 展開して読めることとcall境界の効果が保持されることは別．
        # 未実装の境界は必ず診断する．
        if metadata.get('outputs') or metadata.get('inputs') or raw.get('with') or raw.get('continue-on-error') or (raw.get('if') is not None):
            diagnostic(
                'composite-boundary',
                'composite inputs/outputs/parent guards/continue-on-error require a nested call frame',
                span
            )
        # metadataの行・列も読み，展開後のstepを元action.ymlへ戻せるようにする．
        tree = yaml.compose(source, Loader=WorkflowLoader)
        # metadata内のruns.stepsのnodeを，内部stepのsourceの基準にする．
        snode = base._member(base._member(tree, 'runs'), 'steps')
        # 現在refをstackへ加えて再帰展開し，同じrefへの循環を次の呼出しで検出する．
        children = expand(metadata['runs']['steps'], snode, str(path), registry, diagnostic, stack + (ref,))
        # 呼出しstep idまたは添字をprefixにし，内部idを呼出し位置ごとに区別する．
        prefix = raw.get('id', 'composite' + str(index)) + '__'
        # 内部stepとそのmetadata上の位置を，対応を保ったまま順に取り出す．
        for child, child_span in children:
            # 元metadataの辞書は変更せず，prefix付きidと統合envをコピー側へ設定する．
            child = copy.deepcopy(child)
            # 内部id参照のスコープを完全には保持しないため，id付き内部stepには診断を残す．
            if child.get('id'):
                diagnostic(
                    'composite-step-reference',
                    'internal step id/output scoping is not flattened silently',
                    child_span
                )
            # prefixを付けた内部idをコピー側へ設定し，元metadataの辞書は変更しない．
            child['id'] = prefix + str(child.get('id', len(result)))
            # **で親envの後に子envを展開し，同名の変数は内部stepの指定を優先する．
            child['env'] = {**raw.get('env', {}), **child.get('env', {})}
            # 展開後の設定とmetadata上の位置を組で返し，workflow側の位置へ取り違えないようにする．
            result.append((child, child_span))
    return result
