# GitHub ActionsのYAMLを読むための共通loader．
# イベント宣言のonを文字列として保持し，重複キーを黙って上書きしない．
"""GitHub ActionsのYAMLを読む共通loaderを定義する．

yaml.load/yaml.composeが文字列を読む際にWorkflowLoaderを指定して使う．
SafeLoaderの規則を複製してon/off/yes/noの暗黙Boolean化を外し，true/falseだけを登録する．
mappingの構築では重複キーを例外にし，後の値で黙って上書きされることを防ぐ．
このmoduleの読込み時に専用classへ規則を登録するが，SafeLoader本体の規則は直接変更しない．
compiler，frontend，scenario，Compositeや参照BFSから共用される．
"""
import copy
import re
import yaml

class WorkflowLoader(yaml.SafeLoader):
    """GitHub Actions向けのSafeLoader派生class．"""
    pass
# PyYAMLのYAML 1.1ではon/off/yes/noがboolになる．
# GitHubのonキーを保つ．
# SafeLoaderの入れ子のlistまでdeepcopyし，共用loader全体の型変換規則を巻き添えにしない．
WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
# 先頭文字ごとに登録された暗黙型変換の候補列を，専用loader内で順番に見直す．
for key, values in WorkflowLoader.yaml_implicit_resolvers.items():
    # bool用の規則だけをリスト内包表記で除き，数値等の他の変換規則は残す．
    WorkflowLoader.yaml_implicit_resolvers[key] = [(tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:bool"]
# true/falseだけをBooleanにする規則を登録し，on/off/yes/noを文字列として保持する．
WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF")
)

# 同じmapping内の重複キーをエラーにする．
# 後の値での無言の上書きを防ぐ．
def mapping(loader, node):
    """重複キーを禁止してYAML mappingをPython dictへ変換する．"""
    # YAMLのmergeキーを先に展開し，通常のキーと同じ重複検査へ渡す．
    loader.flatten_mapping(node)
    # このmapping nodeから構築するKey/Valueの辞書を空で用意する．
    result = {}
    # mappingの各要素はキーnodeと値nodeの組なので，2つへアンパックして走査する．
    for key_node, value_node in node.value:
        # YAMLのキーnodeをPython値へ変換し，辞書の重複検査に使う．
        key = loader.construct_object(key_node)
        # dictへ格納する前に重複を調べる．
        # 位置の表示ではPyYAMLの0始まり行に1を足す．
        if key in result:
            raise ValueError(f'duplicate YAML key {key!r}, line {key_node.start_mark.line + 1}')
        # 重複がないキーだけを値nodeの変換結果へ対応付け，Pythonの辞書にする．
        result[key] = loader.construct_object(value_node)
    return result
# mapping nodeを読む際にこの重複検査付き関数が使われるよう，専用loaderへ登録する．
WorkflowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
