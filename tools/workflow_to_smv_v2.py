#!/usr/bin/env python3
# YAMLの入力パスと出力先を受け取り，共通の変換器を呼ぶ入口．
# scenarioを省略した場合は外部条件の有限候補を探索する．
# ここではNuSMVによる検証は実行しない．
"""YAMLからSMVを生成するコマンドの入口．

入力はproducer/consumerのYAML，追加workflow，任意のscenario JSON，出力先である．
argparseで引数をPath等へ変換し，compiler.compile_workflowsへまとめて渡す．
戻ったSMV文字列とmanifestを別fileへ保存する．既存の出力は上書きしない．
NuSMVの実行やGitHub Actionsのjob実行は行わない．検証はverificationへ分担する．
この説明文はargparseのhelpにも使われる．
"""
import argparse
import json
from pathlib import Path
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.frontend import WorkflowError

# 出力の重複を確認してから，SMVとsource mapを保存する．
def main():
    """CLI引数を受け，共通変換器の結果を新規fileへ保存する．"""
    # ファイル冒頭の説明を--helpにも表示する引数解析器を作る．
    parser = argparse.ArgumentParser(description=__doc__)
    # Producerのworkflow YAMLを必須の位置引数として受け取り，type=Pathでパス文字列をPathオブジェクトへ変換する．
    parser.add_argument('producer', type=Path)

    # Consumerは任意の位置引数とし，省略時は最初のworkflowだけを変換する．
    parser.add_argument('consumer', type=Path, nargs='?')

    # 追加のworkflow YAMLを受け取り，action='append'で指定のたびにリストへ追加する．
    # 指定がなければdefault=[]の空リストとなり，ProducerとConsumerの2本だけを変換する．
    parser.add_argument(
        '--workflow',
        type=Path,
        action='append',
        default=[],
        help='additional bounded workflow run'
    )

    # 部分条件または従来の完全なscenario JSONを受け取り，省略時は有限候補を自動生成する．
    parser.add_argument(
        '--scenario',
        type=Path,
        help='partial condition restrictions or a complete legacy scenario'
    )

    # SMVの保存先を受け取り，required=Trueで--outputの指定を必須にする．
    parser.add_argument('--output', type=Path, required=True)

    # 対応表の保存先を受け取り，dest='map_path'によりargs.map_pathという名前で参照する．
    parser.add_argument('--map', dest='map_path', type=Path)

    # 実際のコマンドラインを解析し，登録した引数を属性として持つargsへ格納する．
    # 条件を省略した場合は候補を制限せず，指定した場合だけ共通IRへ制約を渡す．
    parser.add_argument('--run', dest='condition_run', help='condition target: run ID or unique workflow name; default run0')
    for option in ('event','activity','head-branch','base-branch','default-branch','repository','repository-visibility','runner-os','runner-arch'):
        parser.add_argument('--'+option)
    for option in ('fork','send-write-tokens','workflow-present'):
        # 文字列falseをPythonのtruthyとして扱わず，明示的にBooleanへ変換する．
        parser.add_argument('--'+option,choices=('true','false'))
    args = parser.parse_args()

    # --mapを省略した場合は，SMVの保存先に.map.jsonを付けたパスを使う．
    mapping = args.map_path or Path(str(args.output) + '.map.json')

    # SMV本体とsource mapの両方を調べ，既存の結果を上書きしないようにする．
    for target in (args.output, mapping):
        # exists()で通常のファイル・ディレクトリを調べ，is_symlink()でリンク先がないシンボリックリンクも検出する．
        if target.exists() or target.is_symlink():
            # 既存ファイルが見つかったため，引数エラーとして表示し，変換・保存の前に終了する．
            parser.error(f'output already exists; use a new output path: {target}')

    try:
        # scenario指定時だけJSONを読み，省略時はNoneを渡して既定条件の生成を依頼する．
        scenario = json.loads(args.scenario.read_text()) if args.scenario else None
        # *args.workflowで追加パスをリスト内へ展開し，返された2要素をSMV文字列と対応表へアンパックする．
        restriction = {'run':args.condition_run or 'run0'}
        for name in ('event','activity','head-branch','base-branch','default-branch','repository','repository-visibility','runner-os','runner-arch','fork','send-write-tokens','workflow-present'):
            value = getattr(args,name.replace('-','_'))
            if value is not None:
                restriction[name] = value=='true' if name in {'fork','send-write-tokens','workflow-present'} else value
        smv, manifest = compile_workflows([args.producer, *([args.consumer] if args.consumer else []), *args.workflow], scenario,
                                          restrictions=[restriction] if len(restriction)>1 else None)
    # 入力形式・読み書き・JSON等のエラーをCLIのエラー表示へまとめ，ここでは成功として扱わない．
    except (WorkflowError, OSError, ValueError) as exc:
        parser.error(str(exc))

    # SMVの親ディレクトリを必要な階層まで作り，既に存在する場合はそのまま利用する．
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # source map側も別の保存先を指定できるため，その親ディレクトリを独立に用意する．
    mapping.parent.mkdir(parents=True, exist_ok=True)
    # 変換器が返したSMV文字列を保存し，ここではNuSMVによる検証は実行しない．
    args.output.write_text(smv)
    # 対応表を日本語を保持するJSONへ整形し，末尾に改行を付けて保存する．
    mapping.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'生成SMV: {args.output}\n対応表・解析条件: {mapping}')
    if args.scenario is None:
        print('入力条件: YAMLから外部条件の有限候補を自動構成し，FROZENVARで探索します．仮定とcoverageは対応表に記録します．')
    if manifest['diagnostics']:
        codes = ', '.join(sorted({item['code'] for item in manifest['diagnostics']}))
        print(f'注意: {manifest["verdict"]}; 診断: {codes}．CTLのtrueだけで安全と判断しないでください．')
    # 正常終了を表す整数0を，スクリプト末尾のSystemExitへ返す．
    return 0
# importされた場合には実行せず，このファイルを直接起動した場合だけCLIへ進む．
if __name__ == '__main__':
    # main()の戻り値をプロセスの終了コードとして呼出し元へ返す．
    raise SystemExit(main())
