# 対応するAction参照を，モデルが扱う効果契約へ対応付ける．
# 契約はAction全体の解析結果ではない．
# 未登録refを同名の対応版として扱わない．
"""Action参照と，対応する限定効果契約の識別情報を対応付ける．

入力はusesの文字列，出力はActionContractまたは未登録を示すNoneである．
Action名とrefを分け，登録表へ完全一致させてmajor/runtime/metadata参照を返す．
共通dispatch名が同じでも，refや契約identityまで同一だとは扱わない．
外部Actionの取得・実行・任意コード解析は行わず，compilerがこの契約の効果subsetを使用する．
"""
from dataclasses import asdict, dataclass

@dataclass(frozen=True)
class ActionContract:
    """登録Actionのref・runtime・限定効果の識別情報．"""
    # 契約の同一性を表し，Action名が同じでもrefの違う契約を区別する．
    identity: str
    action: str
    # usesの@以降を保持し，tagかcommit参照かも含めて登録表へ完全一致させる．
    reference: str
    major: int
    # 登録された版に対応するruntime名を記録し，現在の環境から推測しない．
    runtime: str
    # compilerが分岐に使う限定効果名であり，Action全機能への対応を意味しない．
    effect: str
    metadata: str
# Action名ごとに受け付けるrefとmajorを列挙し，未知refを同じActionの既知版で代用しない．
REFERENCES = {
    'actions/checkout': {'v4': 4, '11d5960a326750d5838078e36cf38b85af677262': 4},
    'actions/upload-artifact': {'v4': 4, 'ea165f8d65b6e75b540449e92b4886f43607fa02': 4},
    'actions/download-artifact': {'v4': 4, 'd3f86a106a0bac45b974a628896c90dbdf5c8093': 4},
    'actions/cache': {'v4': 4, 'v5.0.4': 5},
    'actions/cache/save': {'v4': 4},
    'actions/cache/restore': {'v4': 4}
}

# usesのAction名とrefを登録表へ照合し，契約情報を返す．
# 未登録refは解決しない．
def resolve(uses):
    """uses文字列を登録表の版付き契約へ対応付ける．"""
    # rpartitionは末尾の@で3分割する．
    # _はここでは使わない区切り文字を受ける慣習的な変数名．
    action, _, reference = uses.rpartition('@')
    # Action名がなければ空辞書，refがなければNoneを返し，2段階で契約を探す．
    major = REFERENCES.get(action, {}).get(reference)
    # 未登録参照なら契約を返さず，compiler側の未対応診断に任せる．
    if major is None:
        return None
    # actions/cache/saveなら先頭2要素をactions/cacheというrepository名にする．
    repo = '/'.join(action.split('/')[:2])
    # actions/cache/saveのsave部分を取り出し，repository内のmetadataパスへ使う．
    suffix = '/'.join(action.split('/')[2:])
    # 契約が参照するaction.ymlのURL文字列を作るだけで，ネットワークから取得はしない．
    metadata = f'https://raw.githubusercontent.com/{repo}/{reference}/' + (suffix + '/' if suffix else '') + 'action.yml'
    # 共通dispatch名は，共有する効果subsetだけを表す．
    # 契約identity・runtime・ref・metadataは別の情報として保存する．
    return ActionContract(
        f'{action}@{reference}:bounded-effects-v1',
        action,
        reference,
        major,
        'node24' if major == 5 else 'node20',
        action + '-v4',
        metadata
    )
