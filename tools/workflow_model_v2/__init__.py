# workflowの意味規則とSMV生成をまとめたパッケージ．
# 変換の共通入口はcompiler.compile_workflows．
"""GitHub Actionsの有限状態モデルを扱うパッケージ．

このfile自体には入出力や初期化処理がなく，説明だけを置く．
変換の入口はcompiler.compile_workflows，SMV出力はtransition，検証実行はverificationである．
CLIと評価処理は同じ変換器を使い，scenarioの指定は入力条件だけを変える．
"""
