# Artifactの限定subsetを，生成SMVとは別の状態探索で照合する．
# YAMLを直接読み，待ち行列による幅優先探索（BFS）で到達状態を列挙する．
# 一致は対応subset内の実装間照合であり，GitHub全体の意味論を証明するものではない．
"""Artifactのdownload/use subsetを独立の幅優先探索で調べる．

入力はproducer/consumerのYAML，出力は到達数・転送と依存利用の可否・明示仮定である．
frontendの型付きYAMLと限定Shell構文を読み，immutableなStateをqueueへ積んで列挙する．
各workflowに1 job，upload/download各1つ，成功条件を固定した範囲だけを扱う．
完全性確認・権限sink・実サービスのID照合はこの参照探索の対象ではない．
生成SMVを読まず，テスト等から変換器とは別の照合材料として使う．
"""
from __future__ import annotations
import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any
TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from . import frontend as base
# noqa: E402
from workflow_shell_subset import UnsupportedShell, parse_shell

# noqa: E402
class BFSUnsupported(ValueError):
    """参照BFSの限定範囲に入らない入力を通知する例外．"""
    pass

@dataclass(frozen=True)
class State:
    """queue/setに入れる，Artifact参照探索の状態．"""
    producer_pc: int
    consumer_pc: int
    uploaded: bool
    download: str
    dependent_use: bool

def _action(step: base.Step) -> str:
    """usesから@以後のrefを外してAction名を得る．"""
    return step.uses.rsplit("@", 1)[0] if step.uses else ""

def _eval(expr: base.Expr | None, env: dict[str, Any]) -> Any:
    """参照BFSの固定contextで限定式を評価する．"""
    if expr is None:
        return True
    if expr.kind in {"bool", "string"}:
        return expr.value
    if expr.kind == "ref":
        if expr.value not in env:
            raise BFSUnsupported(f'unknown expression reference {expr.value!r}')
        return env[expr.value]
    if expr.kind == "call":
        if expr.value == "success":
            return True
        raise BFSUnsupported(f'unknown status function {expr.value!r}')
    if expr.kind == "not":
        return not bool(_eval(expr.left, env))
    if expr.kind in {"&&", "||"}:
        left, right = (bool(_eval(expr.left, env)), bool(_eval(expr.right, env)))
        return left and right if expr.kind == "&&" else left or right
    if expr.kind in {"==", "!="}:
        left, right = (_eval(expr.left, env), _eval(expr.right, env))
        return left == right if expr.kind == "==" else left != right
    raise BFSUnsupported(f'unsupported expression node {expr.kind!r}')

def _permission(wf: base.Workflow, job: base.Job, scope: str) -> str | None:
    """job/workflowの明示宣言から参照BFS用のscope権限を得る．"""
    # jobに権限宣言があれば優先し，なければworkflow-level宣言を参照する．
    source = job.permissions if job.permissions_declared else wf.permissions
    # 明示権限が両方にない場合は未知としてNoneを返し，安全側のnoneを勝手に補わない．
    if not (job.permissions_declared or wf.permissions_declared):
        return None
    source = source or {}
    # 全scope共通指定があれば使い，なければscope指定，さらに未指定ならnoneへする．
    return source.get("*", source.get(scope, "none"))

def _under(download_path: str, file_path: str) -> bool:
    """fileがdownload rootと同じか配下かを文字列で調べる．"""
    root = download_path.rstrip("/")
    return file_path == root or file_path.startswith(root + "/")

def _workflow_run_matches(producer: base.Workflow, consumer: base.Workflow) -> bool:
    """consumerのworkflow_run名とcompleted宣言を照合する．"""
    cfg = consumer.events.get("workflow_run")
    if not isinstance(cfg, dict):
        return False
    names = cfg.get("workflows", [producer.name])
    types = cfg.get("types", ["requested", "in_progress", "completed"])
    names = [names] if isinstance(names, str) else names
    types = [types] if isinstance(types, str) else types
    if producer.name not in names or "completed" not in types:
        return False
    branches = cfg.get("branches", [])
    branches = [branches] if isinstance(branches, str) else branches
    if any((any((c in str(v) for c in "*?![]")) for v in branches)):
        raise BFSUnsupported("workflow_run branch glob is outside this BFS subset")
    return True

# producer/consumerを直接読み，転送・利用・模擬sink等への到達を独立に列挙する．
def explore(producer_path: Path, consumer_path: Path) -> dict[str, Any]:
    """producer/consumerの有限状態をBFSで列挙する．"""
    # 2本のYAMLを参照用Typed IRへ読み，変換器のSMVではなく入力の構造から探索する．
    producer, consumer = (base.load_workflow(producer_path), base.load_workflow(consumer_path))
    # この探索は各workflowに1 Jobのsubsetなので，それ以外はBFSUnsupportedにする．
    if len(producer.jobs) != 1 or len(consumer.jobs) != 1:
        raise BFSUnsupported("requires exactly one job per workflow")
    pj, cj = (producer.jobs[0], consumer.jobs[0])
    if pj.needs or cj.needs:
        raise BFSUnsupported("job needs are outside this BFS subset")
    event = "pull_request" if "pull_request" in producer.events else "push"
    if event not in producer.events or not _workflow_run_matches(producer, consumer):
        raise BFSUnsupported("producer event or completed workflow_run does not match")
    event_cfg = producer.events[event] or {}
    if not isinstance(event_cfg, dict):
        raise BFSUnsupported("producer event config is outside this BFS subset")
    for key in ("branches", "branches-ignore"):
        values = event_cfg.get(key, [])
        values = [values] if isinstance(values, str) else values
        if any((any((c in str(v) for c in "*?![]")) for v in values)):
            raise BFSUnsupported("producer branch glob is outside this BFS subset")
    # producerのupload-artifact stepだけを集め，候補が一意かを次で確認する．
    uploads = [s for s in pj.steps if _action(s) == "actions/upload-artifact"]
    # consumerのdownload-artifact stepを集め，同名・run-id・tokenの照合に使う．
    downloads = [s for s in cj.steps if _action(s) == "actions/download-artifact"]
    if len(uploads) != 1 or len(downloads) != 1:
        raise BFSUnsupported("requires exactly one upload and one download")
    upload, download = (uploads[0], downloads[0])
    # upload/downloadのwith.nameを取り出し，実行時に同じ保存物を取得できるか比較する．
    upload_name, download_name = (upload.inputs.get("name"), download.inputs.get("name"))
    download_path = download.inputs.get("path", base.GH_EXPR_PREFIX + " github.workspace }}")
    run_id, token = (download.inputs.get("run-id"), download.inputs.get("github-token"))
    if not all((isinstance(x, str) for x in (upload_name, download_name, download_path, run_id, token))):
        raise BFSUnsupported("dynamic artifact input is outside this BFS subset")
    if run_id != base.GH_EXPR_PREFIX + " github.event.workflow_run.id }}" or token not in {
        base.GH_EXPR_PREFIX + " secrets.GITHUB_TOKEN }}",
        base.GH_EXPR_PREFIX + " github.token }}"
    }:
        raise BFSUnsupported("download run-id/token is outside the bounded contract")
    # consumerの明示actions権限を求め，この参照subsetに必要なread/writeを確認する．
    action_permission = _permission(consumer, cj, "actions")
    if action_permission not in {"read", "write"}:
        raise BFSUnsupported("effective actions permission is not explicitly read/write")
    # 元workflowパスとstep番号をキーにし，構造化ShellのIRを保持する．
    programs = {}
    for wf, job in ((producer, pj), (consumer, cj)):
        # このJobのstepを元の順序で走査し，各stepの契約・値・sourceを参照する．
        for step in job.steps:
            if step.run is None:
                continue
            try:
                line = step.run_span.line + 1 if step.run_span else step.span.line
                # run本文を限定Shellの操作IRへ読み，内容readがdownload先配下かを後で判定する．
                programs[wf.path, step.index] = parse_shell(step.run, line)
            except UnsupportedShell as exc:
                raise BFSUnsupported(f'unsupported complete Shell at {wf.path}:{exc.span.line}') from exc
    # この参照探索ではpath filter成立と，選択したproducer eventを仮定する．
    # stepは成功し，workflow_runは成功した上流のcompletedである条件を固定する．
    env = {
        "github.event_name": "workflow_run",
        "github.event.workflow_run.event": event,
        "github.event.workflow_run.conclusion": "success",
        "github.event.workflow_run.name": producer.name
    }
    if not _eval(pj.condition, env) or not _eval(cj.condition, env):
        raise BFSUnsupported("job condition is false under the selected event")
    # 両Jobのstep数を終端PC値として使い，範囲内の位置だけを実行する．
    p_count, c_count = (len(pj.steps), len(cj.steps))
    # この探索のPCは-2が未開始，-1が準備後，0以降がstep番号，step数が完了を表す．
    initial = State(-2, -2, False, "not_attempted", False)
    # 同じ初期Stateを訪問済み集合と待ち行列へ入れ，重複検査と探索順を別々に管理する．
    reached, queue = ({initial}, [initial])
    edges = 0
    # queueから先に登録した状態を取り出し，両runの可能な進行から後継状態を作る幅優先探索．
    while queue:
        # 最も先に登録したStateを取り出し，幅優先探索の順序を守る．
        state = queue.pop(0)
        # producerまたはconsumerを1回進めた後継状態を集める．
        successors = []
        # 状態から両runのPCを取り出し，どちらが次のstepへ進めるかを別々に判定する．
        ppc, cpc = (state.producer_pc, state.consumer_pc)
        # 後継状態を複数作ることで，schedulerがどちらのrunを進めるかを列挙する．
        if ppc == -2:
            successors.append(State(-1, cpc, state.uploaded, state.download, state.dependent_use))
        elif ppc == -1:
            successors.append(State(0, cpc, state.uploaded, state.download, state.dependent_use))
        elif ppc < p_count:
            step = pj.steps[ppc]
            enabled = bool(_eval(step.condition, env))
            # upload条件が真で保存名も一致した場合に保存済みにし，既存の保存履歴も残す．
            uploaded = state.uploaded or (enabled and _action(step) == "actions/upload-artifact" and (upload_name == download_name))
            successors.append(State(ppc + 1, cpc, uploaded, state.download, state.dependent_use))
        if cpc == -2:
            # consumerの準備はproducer全step完了後だけにし，completed workflow_runの順序を表す．
            if ppc == p_count:
                successors.append(State(ppc, -1, state.uploaded, state.download, state.dependent_use))
            else:
                successors.append(state)
        elif cpc == -1:
            successors.append(State(ppc, 0, state.uploaded, state.download, state.dependent_use))
        elif cpc < c_count:
            step = cj.steps[cpc]
            enabled = bool(_eval(step.condition, env))
            # download結果と利用履歴の旧値を取り出し，今回のstep効果だけを加える．
            download_state, used = (state.download, state.dependent_use)
            action = _action(step)
            if enabled and action == "actions/download-artifact":
                # upload済みかつ名前一致の場合だけ成功へし，それ以外の取得試行はfailedへする．
                download_state = "succeeded" if state.uploaded and upload_name == download_name else "failed"
            elif enabled and step.run is not None:
                program = programs[consumer.path, step.index]
                for op in program.artifact_reads:
                    if any(((file_path := step.env.get(var)) is not None and _under(str(download_path), str(file_path)) and (state.download == "succeeded") for var in op.reads)):
                        # download成功後に配下fileを読む操作へ到達したため，Artifact依存利用の履歴を立てる．
                        used = True
            successors.append(State(ppc, cpc + 1, state.uploaded, download_state, used))
        # 両runの可能な次状態を走査し，新しいStateだけを待ち行列へ追加する．
        for successor in successors:
            edges += 1
            # immutable Stateをsetで重複除去する．
            # 既知の状態を繰返しqueueへ追加しない．
            if successor not in reached:
                # 訪問済みにしてからqueueへ入れ，同じStateが複数回追加されるのを防ぐ．
                reached.add(successor)
                queue.append(successor)
    return {
        "semantics": "yaml-operational-bfs-download-use-subset-v1",
        "sources": [
            {"path": producer.path, "sha256": producer.sha256},
            {"path": consumer.path, "sha256": consumer.sha256}
        ],
        "scenario": {
            "producerEvent": event,
            "producerPathFilter": "true (fixed unknown input)",
            "stepOutcomes": "all success",
            "workflowRunActivity": "completed",
            "actionPermission": action_permission
        },
        "reachableProjectedStateCount": len(reached),
        "exploredEdges": edges,
        "downloadReachable": any((s.download == "succeeded" for s in reached)),
        "artifactDependentUseReachable": any((s.dependent_use for s in reached)),
        "useRequiresPriorSuccessfulDownload": all((not s.dependent_use or s.download == "succeeded" for s in reached)),
        "limits": [
            "one job per workflow and one upload/download",
            "only Artifact-file reads count as dependent use",
            "integrity, sink, actual run ID/artifact ID, and runner behavior are excluded"
        ]
    }

def main() -> int:
    """Artifact参照BFSをCLIから実行してJSONへ保存する．"""
    # 受け付ける引数を登録する解析器を作り，必須指定・型変換・help表示を管理する．
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("producer", type=Path)
    parser.add_argument("consumer", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    # 指定されたコマンドラインを解析し，各引数をargsの属性へ格納する．
    args = parser.parse_args()
    result = explore(args.producer, args.consumer)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
